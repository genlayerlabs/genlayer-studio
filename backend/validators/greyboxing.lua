local lib = require("lib-genvm")
local llm = require("lib-llm")

-- check https://github.com/genlayerlabs/genvm/blob/v0.1.2/executor/modules/implementation/scripting/llm-default.lua

-- Used to look up mock responses for testing
-- It returns the response that is linked to a substring of the message
local function get_mock_response_from_table(table, message)
	for key, value in pairs(table) do
		if string.find(message, key) then
			return value
		end
	end
	return "no match"
end

local function just_in_backend(ctx, args, mapped_prompt)
	---@cast mapped_prompt MappedPrompt
	---@cast args LLMExecPromptPayload | LLMExecPromptTemplatePayload

	-- Return mock response if it exists
	if ctx.host_data.mock_response then
		local result

		if args.template == "EqComparative" then
			-- Return the matching response to gl.eq_principle_prompt_comparative request which contains a principle key in the payload
			result = get_mock_response_from_table(ctx.host_data.mock_response.eq_principle_prompt_comparative, mapped_prompt.prompt.user_message)
		elseif args.template == "EqNonComparativeValidator" then
			-- Return the matching response to gl.eq_principle_prompt_non_comparative request which contains an output key in the payload
			result = get_mock_response_from_table(ctx.host_data.mock_response.eq_principle_prompt_non_comparative, mapped_prompt.prompt.user_message)
		else
			-- Return the matching response to gl.exec_prompt request which does not contain any specific key in the payload
			-- EqNonComparativeLeader is essentially just exec_prompt
			result = get_mock_response_from_table(ctx.host_data.mock_response.response, mapped_prompt.prompt.user_message)
		end
		lib.log{level = "debug", message = "executed with", type = type(result), res = result}
		return result
	end

	local provider_id = ctx.host_data.studio_llm_id
	local model = lib.get_first_from_table(llm.providers[provider_id].models).key

	mapped_prompt.prompt.use_max_completion_tokens = false

	for i = 1,3 do
		local request = {
			provider = provider_id,
			model = model,
			prompt = mapped_prompt.prompt,
			format = mapped_prompt.format,
		}

		local success, result = pcall(function ()
			return llm.rs.exec_prompt_in_provider(
				ctx,
				request
			)
		end)

		lib.log{level = "debug", message = "executed with", success = success, type = type(result), res = result}
		if success then
			return result
		end

		local as_user_error = lib.rs.as_user_error(result)
		if as_user_error == nil then
			error(result)
		end

		if llm.overloaded_statuses[as_user_error.ctx.status] then
			lib.log{level = "warning", message = "service is overloaded", error = as_user_error, request = request}
		else
			lib.log{level = "error", message = "provider failed", error = as_user_error, request = request}
			as_user_error.fatal = true

			lib.rs.user_error(as_user_error)
		end

		lib.log{level = "warning", message = "sleeping before retry"}

		lib.rs.sleep_seconds(1.5)
	end

	lib.rs.user_error({
		causes = {"NO_PROVIDER_FOR_PROMPT"},
		fatal = true,
		ctx = {
			prompt = mapped_prompt,
			host_data = ctx.host_data,
		}
	})
end

function ExecPrompt(ctx, args)
	---@cast args LLMExecPromptPayload

	local mapped = llm.exec_prompt_transform(args)

	return just_in_backend(ctx, args, mapped)
end

function ExecPromptTemplate(ctx, args)
	---@cast args LLMExecPromptTemplatePayload

	local template = args.template -- workaround by kp2pml30 (Kira) GVM-86
	local mapped = llm.exec_prompt_template_transform(args)
	args.template = template

	return just_in_backend(ctx, args, mapped)
end
