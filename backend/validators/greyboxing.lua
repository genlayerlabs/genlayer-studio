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

local function handle_custom_plugin(ctx, args, mapped_prompt)
	local custom_plugin_data = ctx.host_data.custom_plugin_data
	local content
	if mapped_prompt.prompt.system_message then
		content = mapped_prompt.prompt.system_message .. mapped_prompt.prompt.user_message
	else
		content = mapped_prompt.prompt.user_message
	end
	local payload = {
		model = custom_plugin_data.model,
		messages = {
			{role = "user", content = content}
		}
	}

	-- Convert config values to appropriate types because we could only send strings to genvm
	for k, v in pairs(custom_plugin_data.config) do
		local num = tonumber(v)
		if num then
			payload[k] = num
		elseif v == "true" then
			payload[k] = true
		elseif v == "false" then
			payload[k] = false
		else
			payload[k] = v
		end
	end

	local api_request = lib.rs.request(ctx, {
		method = 'POST',
		url = custom_plugin_data.plugin_config.api_url,
		headers = {
			["Content-Type"] = "application/json",
			["Authorization"] = "Bearer " .. custom_plugin_data.plugin_config.api_key_env_var
		},
		body = lib.rs.json_stringify(payload),
		json = true
	})

	local response_data = api_request.body
	local response_status = api_request.status

	if response_status ~= 200 then
		return false, lib.rs.json_stringify(response_data.error)
	end

	local result
	if args.template == "EqComparative" or args.template == "EqNonComparativeValidator" then
		-- These equivalence principles ask also for a reasoning but we only want the result
		local json_response = lib.rs.json_parse(response_data.choices[1].message.content)
		result = json_response.result
	else
		result = response_data.choices[1].message.content
	end

	return true, result
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

	local max_attempts_per_provider = 3
	mapped_prompt.prompt.use_max_completion_tokens = false

	-- First: Try primary model (3 attempts)
	local primary_provider_id = ctx.host_data.studio_llm_id
	local primary_model = lib.get_first_from_table(llm.providers[primary_provider_id].models).key
	if primary_provider_id then
		for attempt = 1, max_attempts_per_provider do
			local success, result
			if ctx.host_data.custom_plugin_data then
				success, result = handle_custom_plugin(ctx, args, mapped_prompt)
			else
				local request = {
					provider = primary_provider_id,
					model = primary_model,
					prompt = mapped_prompt.prompt,
					format = mapped_prompt.format,
				}

				success, result = pcall(function ()
					return llm.rs.exec_prompt_in_provider(
						ctx,
						request
					)
				end)
			end

			lib.log{level = "debug", message = "executed with", success = success, type = type(result), res = result}
			if success and result then
				return result
			end

			local as_user_error = lib.rs.as_user_error(result)
			if as_user_error == nil then
				error(result)
			end

			if llm.overloaded_statuses[as_user_error.ctx.status] then
				lib.log{level = "warning", message = "service is overloaded", error = as_user_error, request = request}
			else
				lib.log{level = "warning", message = "provider failed", error = as_user_error, request = request}
			end

			lib.log{level = "warning", message = "sleeping before retry"}

			lib.rs.sleep_seconds(1.5)
		end
	end

	-- Second: Try fallback model (3 attempts) if available
	local fallback_provider_id = ctx.host_data.fallback_llm_id
	local fallback_model = lib.get_first_from_table(llm.providers[fallback_provider_id].models).key
	if fallback_provider_id then
		lib.log{level = "debug", message = "Switching to fallback model"}

		for attempt = 1, max_attempts_per_provider do
			local success, result
			if ctx.host_data.custom_plugin_data then
				success, result = handle_custom_plugin(ctx, args, mapped_prompt)
			else
				local request = {
					provider = fallback_provider_id,
					model = fallback_model,
					prompt = mapped_prompt.prompt,
					format = mapped_prompt.format,
				}

				success, result = pcall(function ()
					return llm.rs.exec_prompt_in_provider(
						ctx,
						request
					)
				end)
			end

			lib.log{level = "debug", message = "executed with", success = success, type = type(result), res = result}
			if success and result then
				return result
			end

			local as_user_error = lib.rs.as_user_error(result)
			if as_user_error == nil then
				error(result)
			end

			if llm.overloaded_statuses[as_user_error.ctx.status] then
				lib.log{level = "warning", message = "service is overloaded", error = as_user_error, request = request}
			else
				lib.log{level = "warning", message = "provider failed", error = as_user_error, request = request}
			end

			lib.log{level = "warning", message = "sleeping before retry"}

			lib.rs.sleep_seconds(1.5)
		end
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
