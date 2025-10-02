local lib = require('lib-genvm')
local web = require('lib-web')

local function status_is_good(status)
	return status >= 200 and status < 300 or status == 304
end

function Render(ctx, payload)
    ---@cast payload WebRenderPayload
    web.check_url(payload.url)

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
