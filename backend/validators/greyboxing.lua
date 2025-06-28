local lib = require("lib-greyboxing")
local inspect = require("inspect")


-- Used to look up mock responses for testing
-- It returns the response that is linked to a substring of the message
function get_mock_response_from_table(table, message)
	for key, value in pairs(table) do
		if string.find(message, key) then
			return value
		end
	end
	return "no match"
end

function just_in_backend(args, prompt, format)
	local search_in = lib.select_backends_for(args, format)

	lib.log{ args = args, prompt = prompt, format = format, search_in = search_in }

	local handler = args.handler

	-- Return mock response if it exists
	if args.host_data and args.host_data.mock_response then
		if args.payload then
			if args.payload.principle then
				-- Return the matching response to gl.eq_principle_prompt_comparative request which contains a principle key in the payload
				result = get_mock_response_from_table(args.host_data.mock_response.eq_principle_prompt_comparative, prompt.user_message)
			elseif args.payload.output then
				-- Return the matching response to gl.eq_principle_prompt_non_comparative request which contains an output key in the payload
				result = get_mock_response_from_table(args.host_data.mock_response.eq_principle_prompt_non_comparative, prompt.user_message)
			else
				-- Return the matching response to gl.exec_prompt request which does not contain any specific key in the payload
				result = get_mock_response_from_table(args.host_data.mock_response.response, prompt.user_message)
			end
		end
		lib.log{level = "debug", message = "executed with", type = type(result), res = result}
		return result
	end

	local provider_id = args.host_data.studio_llm_id
	local model = lib.get_first_from_table(greyboxing.available_backends[provider_id].models).key

	prompt.use_max_completion_tokens = false

	for i = 1,3 do
		local success, result = pcall(function ()
			return lib.exec_in_backend(
				args.handler,
				{
					provider = provider_id,
					model = model,
					prompt = prompt,
					format = format,
				}
			)
		end)

		lib.log{level = "debug", message = "executed with", type = type(result), res = result}
		if success then
			return result
		elseif result.kind == "Overloaded" then
			-- nothing/continue
			lib.log{level = "warning", message = "service is overloaded", result = result}
		else
			lib.log{level = "error", message = "provider failed", result = result}

			error(result)
		end

		lib.log{level = "error", message = "sleeping before retry"}

		lib.sleep_seconds(1.5)
	end
end

function exec_prompt(args)
	local handler = args.handler

	local mapped = lib.exec_prompt_transform(args)

	return just_in_backend(args, mapped.prompt, mapped.format)
end

function exec_prompt_template(args)
	local handler = args.handler

	local mapped = lib.exec_prompt_template_transform(args)

	return just_in_backend(args, mapped.prompt, mapped.format)
end
