local lib = require("lib-genvm")
local llm = require("lib-llm")

-- Maximum output tokens for LLM responses
local MAX_OUTPUT_TOKENS = 4000

llm.exec_prompt_template_transform = function(args)
	lib.log{level = "debug", message = "exec_prompt_template_transform", args = args}

	my_data = {
		EqComparative = { template_id = "eq_comparative", format = "bool" },
		EqNonComparativeValidator = { template_id = "eq_non_comparative_validator", format = "bool" },
		EqNonComparativeLeader = { template_id = "eq_non_comparative_leader", format = "text" },
	}

	my_data = my_data[args.template]
	local my_template = llm.rs.templates[my_data.template_id]

	args.template = nil
	local vars = args

	local as_user_text = my_template.user
	for key, val in pairs(vars) do
		local val_escaped = string.gsub(val, "%%", "%%%%")
		as_user_text = string.gsub(as_user_text, "#{" .. key .. "}", val_escaped)
	end

	local format = my_data.format

	local mapped_prompt = {
		system_message = my_template.system,
		user_message = as_user_text,
		temperature = 0.7,
		images = {},
		max_tokens = MAX_OUTPUT_TOKENS,
		use_max_completion_tokens = false,
	}

	return {
		prompt = mapped_prompt,
		format = format
	}
end

-- check https://github.com/genlayerlabs/genvm/blob/v0.1.2/executor/modules/implementation/scripting/llm-default.lua

-- Used to look up mock responses for testing
-- It returns the response that is linked to a substring of the message
local function get_mock_response_from_table(table, message)
	local default_value = nil
	for key, value in pairs(table) do
		if key == "" then
			-- Save default for later, don't match it now
			default_value = value
		elseif string.find(message, key) then
			return {
				data = value,
				consumed_gen = 0
			}
		end
	end
	-- No specific match found, use default if available
	if default_value ~= nil then
		return {
			data = default_value,
			consumed_gen = 0
		}
	end
	return {
		data = "no match",
		consumed_gen = 0
	}
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

local function try_provider(ctx, args, mapped_prompt, provider_id)
	if not provider_id then
		return nil
	end

	if llm.providers[provider_id] == nil then
		lib.log{ level = "error", message = "provider does not exist", provider_id = provider_id }
	end
	local model_data = lib.get_first_from_table(llm.providers[provider_id].models)
	local model = model_data.key

	mapped_prompt.prompt.use_max_completion_tokens = model_data.value.use_max_completion_tokens
	if model_data.value.meta.config ~= nil and model_data.value.meta.config.temperature ~= nil then
		mapped_prompt.prompt.temperature = model_data.value.meta.config.temperature
	end

	local success, result
	local request
	if ctx.host_data.custom_plugin_data then
		success, result = handle_custom_plugin(ctx, args, mapped_prompt)
	else
		request = {
			provider = provider_id,
			model = model,
			prompt = mapped_prompt.prompt,
			format = mapped_prompt.format,
		}

		success, result = pcall(function ()
			return llm.rs.exec_prompt_in_provider(
				ctx,
				request
			)
		end)

		if success then
			result.consumed_gen = 0

			return result
		end
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

	return nil
end

local function just_in_backend(ctx, args, mapped_prompt)
	---@cast mapped_prompt MappedPrompt
	---@cast args LLMExecPromptPayload | LLMExecPromptTemplatePayload

	-- Return mock response if it exists and matches
	if ctx.host_data.mock_response then
		local mock_data

		if args.template == "EqComparative" then
			-- Return the matching response to gl.eq_principle_prompt_comparative request which contains a principle key in the payload
			mock_data = get_mock_response_from_table(ctx.host_data.mock_response.eq_principle_prompt_comparative, mapped_prompt.prompt.user_message)
		elseif args.template == "EqNonComparativeValidator" then
			-- Return the matching response to gl.eq_principle_prompt_non_comparative request which contains an output key in the payload
			mock_data = get_mock_response_from_table(ctx.host_data.mock_response.eq_principle_prompt_non_comparative, mapped_prompt.prompt.user_message)
		else
			-- Return the matching response to gl.exec_prompt request which does not contain any specific key in the payload
			-- EqNonComparativeLeader is essentially just exec_prompt
			mock_data = get_mock_response_from_table(ctx.host_data.mock_response.response, mapped_prompt.prompt.user_message)
		end

		-- Only return mock response if a match was found, otherwise fall through to real provider
		if mock_data.data ~= "no match" then
			lib.log{level = "debug", message = "executed with mock response", type = type(mock_data), res = mock_data}

			-- Wrap mock response in the same format as exec_prompt_in_provider returns
			-- If response_format is "json", keep tables as-is; otherwise stringify
			local data_value
			if mapped_prompt.format == "json" then
				-- For JSON format, keep tables as tables, parse strings as JSON
				if type(mock_data.data) == "table" then
					data_value = mock_data.data
				else
					-- Try to parse string as JSON
					data_value = lib.rs.json_parse(mock_data.data)
				end
			else
				-- For text format, convert tables to JSON string
				if type(mock_data.data) == "table" then
					data_value = lib.rs.json_stringify(mock_data.data)
				else
					data_value = mock_data.data
				end
			end

			local result = {
				data = data_value,
				consumed_gen = mock_data.consumed_gen
			}
			lib.log{level = "debug", message = "executed with", type = type(result), res = result}
			return result
		else
			lib.log{level = "debug", message = "no mock match found, falling through to real provider"}
		end
	end

	-- First: Try primary model (1 attempts)
	local primary_provider_id = ctx.host_data.studio_llm_id
	local primary_result = try_provider(ctx, args, mapped_prompt, primary_provider_id)
	if primary_result then
		return primary_result
	end

	local primary_model = lib.get_first_from_table(llm.providers[primary_provider_id].models).key
	local fallback_model = nil
	-- Second: Try fallback model (3 attempts) if available
	local fallback_provider_id = ctx.host_data.fallback_llm_id
	if fallback_provider_id then
		fallback_model = lib.get_first_from_table(llm.providers[fallback_provider_id].models).key

		lib.log{level = "warning", message = "switching from primary model " .. primary_model .. " to fallback model " .. fallback_model}
		local fallback_result = try_provider(ctx, args, mapped_prompt, fallback_provider_id)
		if fallback_result then
			return fallback_result
		end
	end

	lib.rs.user_error({
		causes = {"NO_PROVIDER_FOR_PROMPT"},
		fatal = true,
		ctx = {
			prompt = mapped_prompt,
			host_data = ctx.host_data,
			primary_model = primary_model,
			fallback_model = fallback_model,
		}
	})
end

function ExecPrompt(ctx, args, remaining_gen)
	---@cast args LLMExecPromptPayload

	local mapped = llm.exec_prompt_transform(args)

	mapped.prompt.max_tokens = MAX_OUTPUT_TOKENS
	return just_in_backend(ctx, args, mapped)
end

function ExecPromptTemplate(ctx, args, remaining_gen)
	---@cast args LLMExecPromptTemplatePayload

	local template = args.template -- workaround by kp2pml30 (Kira) GVM-86
	local mapped = llm.exec_prompt_template_transform(args)
	args.template = template

	return just_in_backend(ctx, args, mapped)
end
