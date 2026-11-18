-- rpc-blocker.lua
local cjson = require "cjson"

-- Table of specifically restricted methods
local restricted_methods = {
    "sim_clearDbTables",
    "sim_resetStudioState",
    "sim_resetDefaultsLlmProviders",
    "sim_addProvider",
    "sim_updateProvider",
    "sim_deleteProvider",
    "sim_createValidator",
    "sim_createRandomValidator",
    "sim_createRandomValidators",
    "sim_updateValidator",
    "sim_deleteValidator",
    "sim_deleteAllValidators",
    "sim_setFinalityWindowTime"
}

-- Create a lookup table for faster checking
local restricted_lookup = {}
for _, method in ipairs(restricted_methods) do
    restricted_lookup[method] = true
end

-- Main function to handle the filtering
local function filter_rpc_methods()
    ngx.req.read_body()
    local body = ngx.req.get_body_data()

    if not body then
        ngx.status = 400
        ngx.say(cjson.encode({
            error = {
                code = -32700,
                message = "Parse error: empty request body"
            }
        }))
        return ngx.exit(ngx.HTTP_BAD_REQUEST)
    end

    local success, request = pcall(cjson.decode, body)
    if not success then
        ngx.status = 400
        ngx.say(cjson.encode({
            error = {
                code = -32700,
                message = "Parse error: invalid JSON"
            }
        }))
        return ngx.exit(ngx.HTTP_BAD_REQUEST)
    end

    -- Handle both single requests and batch requests
    local requests = request
    if not request[1] then
        requests = {request}
    end

    -- Check each request in the batch
    for _, req in ipairs(requests) do
        if type(req.method) == "string" and restricted_lookup[req.method] then
            ngx.status = 403
            ngx.say(cjson.encode({
                error = {
                    code = -32001,
                    message = "Method not allowed: this method is restricted"
                }
            }))
            return ngx.exit(ngx.HTTP_FORBIDDEN)
        end
    end

    -- If we get here, the request is valid and can be passed through
    return
end

-- Execute the filter
filter_rpc_methods()