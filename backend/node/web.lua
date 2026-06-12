local lib = require('lib-genvm')
local web = require('lib-web')

local function status_is_good(status)
	return status >= 200 and status < 300 or status == 304
end

function Render(ctx, payload)
    ---@cast payload WebRenderPayload
    web.check_url(payload.url)

    -- Return mock render output if it exists and matches.
    if ctx.host_data.mock_web_response and ctx.host_data.mock_web_response.nondet_web_render then
        for url, mock_response_data in pairs(ctx.host_data.mock_web_response.nondet_web_render) do
            if url == payload.url and (not mock_response_data.mode or payload.mode == mock_response_data.mode) then
                lib.log{level = "debug", message = "executed with mock web render response", url = url}
                local status = tonumber(mock_response_data.status or 200)
                if not status_is_good(status) then
                    lib.rs.user_error({
                        causes = {"WEBPAGE_LOAD_FAILED"},
                        fatal = false,
                        ctx = {
                            url = payload.url,
                            status = status,
                            body = mock_response_data.body,
                        }
                    })
                end
                if payload.mode == "screenshot" then
                    return {
                        image = mock_response_data.body
                    }
                else
                    return {
                        text = mock_response_data.body,
                    }
                end
            end
        end
        -- Only log if no match was found, then fall through to real render.
        lib.log{level = "debug", message = "no mock web render response match found, falling through to real render"}
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

    -- Return mock response if it exists and matches
    if ctx.host_data.mock_web_response and ctx.host_data.mock_web_response.nondet_web_request then
        for url, mock_response_data in pairs(ctx.host_data.mock_web_response.nondet_web_request) do
            if url == payload.url and payload.method == mock_response_data.method then
                lib.log{level = "debug", message = "executed with mock web response", url = url}
                return {
                    body = mock_response_data.body,
                    status = mock_response_data.status,
                    headers = {},
                }
            end
        end
        -- Only log if no match was found, then fall through to real request
        lib.log{level = "debug", message = "no mock web response match found, falling through to real request"}
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
