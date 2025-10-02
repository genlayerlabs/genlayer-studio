local lib = require('lib-genvm')
local web = require('lib-web')

-- Used to look up mock web responses for testing
-- Similar to greyboxing.lua's get_mock_response_from_table function
local function get_mock_web_response_from_table(table, url)
    if not table then
        return nil
    end
    for pattern, response in pairs(table) do
        if string.find(url, pattern) then
            return response
        end
    end
    return nil
end

-- Check if web request mocking is enabled
local function should_mock_web_requests(ctx)
    return ctx.host_data and ctx.host_data.mock_web_responses ~= nil
end

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

    -- Return mock response if web mocking is enabled
    if should_mock_web_requests(ctx) then
        local mock_response = get_mock_web_response_from_table(
            ctx.host_data.mock_web_responses.render,
            payload.url
        )
        if mock_response then
            lib.log{level = "debug", message = "using mock web response for render", url = payload.url, response_type = type(mock_response)}

            if payload.mode == "screenshot" then
                return {
                    image = lib.rs.base64_decode(mock_response.image_base64 or "")
                }
            else
                return {
                    text = mock_response.text or mock_response
                }
            end
        end
    end

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

    -- Return mock response if web mocking is enabled
    if should_mock_web_requests(ctx) then
        local mock_response = get_mock_web_response_from_table(
            ctx.host_data.mock_web_responses.request,
            payload.url
        )
        if mock_response then
            lib.log{level = "debug", message = "using mock web response for request", url = payload.url, method = payload.method}
            return {
                status = mock_response.status or 200,
                body = mock_response.body or "",
                headers = mock_response.headers or {}
            }
        end
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