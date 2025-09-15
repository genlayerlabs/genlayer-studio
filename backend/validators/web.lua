local lib = require('lib-genvm')
local web = require('lib-web')

local function render_screenshot(ctx)
    local success, result = pcall(function()
        local result = lib.rs.request(ctx, {
            method = 'GET',
            url = web.rs.config.webdriver_host .. '/session/' .. ctx.session .. '/screenshot',
            headers = {},
            error_on_status = true,
            json = true,
        })

        return {
            image = lib.rs.base64_decode(result.body.value)
        }
    end)

    if not success and ctx.session ~= nil then
        -- Session might have expired, try to get a new one
        ctx.session = web.rs.get_webdriver_session(ctx)
        return render_screenshot(ctx)
    end

    return result
end

local function is_error_page(text)
    -- Check for common error indicators
    if text:match("timeout occurred") or
       text:match("Error code 524") or
       text:match("The origin web server timed out") then
        return true
    end
    return false
end

function Render(ctx, payload)
    ---@cast payload WebRenderPayload
    web.check_url(payload.url)

    if ctx.session == nil then
        ctx.session = web.rs.get_webdriver_session(ctx)
    end

    local function try_request()
        local url_request = lib.rs.request(ctx, {
            method = 'POST',
            url = web.rs.config.webdriver_host .. '/session/' .. ctx.session .. '/url',
            headers = {
                ['Content-Type'] = 'application/json; charset=utf-8',
            },
            body = lib.rs.json_stringify({
                url = payload.url
            }),
        })

        if url_request.status ~= 200 then
            lib.rs.user_error({
                causes = {"WEBPAGE_LOAD_FAILED"},
                fatal = false,
                ctx = {
                    url = payload.url,
                    status = url_request.status,
                    body = url_request.body,
                }
            })
        end

        if payload.wait_after_loaded > 0 then
            lib.rs.sleep_seconds(payload.wait_after_loaded)
        end

        if payload.mode == "screenshot" then
            return render_screenshot(ctx)
        end

        local script
        if payload.mode == "html" then
            script = '{ "script": "return document.body.innerHTML.trim()", "args": [] }'
        else
            script = '{ "script": "return document.body.innerText.replace(/[\\\\s\\\\n]+/g, \\" \\").trim()", "args": [] }'
        end

        local result = lib.rs.request(ctx, {
            method = 'POST',
            url = web.rs.config.webdriver_host .. '/session/' .. ctx.session .. '/execute/sync',
            headers = {
                ['Content-Type'] = 'application/json; charset=utf-8',
            },
            body = script,
            json = true,
            error_on_status = true,
        })

        local text_result = {
            text = result.body.value,
        }

        -- Check if we got an error page
        if is_error_page(text_result.text) then
            error("Timeout or error page detected: " .. text_result.text)
        end

        return text_result
    end

    local success, result = pcall(try_request)

    if not success then
        -- Session might have expired, try to get a new one
        ctx.session = web.rs.get_webdriver_session(ctx)
        return try_request()
    end

    return result
end

function Request(ctx, payload)
    ---@cast payload WebRequestPayload

    web.check_url(payload.url)
    if ctx.host_data.mock_web_response then
        for url, mock_response_data in pairs(ctx.host_data.mock_web_response.nondet_web_request) do
            if url == payload.url and payload.method == mock_response_data.method then
                return {
                    body = mock_response_data.body,
                    status = mock_response_data.status,
                    headers = {},
                }
            end
        end
        return {
            body = "no mock response found",
            status = 404,
            headers = {},
        }
    end

    local function try_request()
        local success, result = pcall(lib.rs.request, ctx, {
            method = payload.method,
            url = payload.url,
            headers = payload.headers,
            body = payload.body,
            sign = payload.sign,
        })

        if success then
            -- Check if we got an error page
            if type(result.body) == "string" and is_error_page(result.body) then
                error("Timeout or error page detected: " .. result.body)
            end
            return result
        end

        lib.reraise_with_fatality(result, false)
    end

    local success, result = pcall(try_request)

    if not success and ctx.session ~= nil then
        -- Session might have expired, try to get a new one
        ctx.session = web.rs.get_webdriver_session(ctx)
        return try_request()
    end

    return result
end