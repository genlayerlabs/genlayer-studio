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

local function status_is_good(status)
	return status >= 200 and status < 300 or status == 304
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

    local url_params = '?url=' .. lib.rs.url_encode(payload.url) ..
        '&mode=' .. payload.mode ..
        '&waitAfterLoaded=' .. tostring(payload.wait_after_loaded or 0)

    local result = lib.rs.request(ctx, {
        method = 'GET',
        url = web.rs.config.webdriver_host .. '/render' .. url_params,
        headers = {},
        error_on_status = true,
    })

    lib.log({
        result = result,
    })

    local status = tonumber(result.headers['resulting-status'])

    if not status_is_good(status) then
        lib.rs.user_error({
            causes = {"WEBPAGE_LOAD_FAILED"},
            fatal = false,
            ctx = {
                url = payload.url,
                status = status,
                body = result.body,
            }
        })
    end

    if payload.mode == "screenshot" then
        return {
            image = result.body
        }
    else
        return {
            text = result.body,
        }
    end
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

    local success, result = pcall(lib.rs.request, ctx, {
        method = payload.method,
        url = payload.url,
        headers = payload.headers,
        body = payload.body,
        sign = payload.sign,
    })

    if success then
        return result
    end

    lib.reraise_with_fatality(result, false)
end