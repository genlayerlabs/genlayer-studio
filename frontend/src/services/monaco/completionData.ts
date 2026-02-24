/**
 * Completion data for GenVM/GenLayer autocomplete
 * Contains method signatures, snippets, and descriptions for all gl.* APIs
 */

export interface MethodSignature {
  params: string;
  snippet: string;
  description: string;
}

export const methodSignatures = {
  // gl root methods
  ContractAt: {
    params: '(address)',
    snippet: '(${1:address})',
    description: 'Get contract proxy at address',
  },

  // gl.eq_principle methods
  strict_eq: {
    params: '(fn)',
    snippet: '(${1:fn})',
    description: 'Strict equality equivalence principle',
  },
  prompt_comparative: {
    params: '(fn, principle)',
    snippet: '(${1:fn}, "${2:principle}")',
    description: 'Comparative equivalence principle using NLP',
  },
  prompt_non_comparative: {
    params: '(fn, task, criteria)',
    snippet: '(${1:fn}, task="${2:task}", criteria="${3:criteria}")',
    description: 'Non-comparative equivalence principle',
  },

  // gl.nondet methods
  exec_prompt: {
    params: '(prompt)',
    snippet: '("${1:prompt}")',
    description: 'Execute an AI prompt',
  },

  // gl.nondet.web methods
  render: {
    params: '(url)',
    snippet: '("${1:url}")',
    description: 'Render a webpage',
  },
  request: {
    params: '(url, method)',
    snippet: '("${1:url}", method="${2:GET}")',
    description: 'Make an HTTP request with specified method',
  },
  get: {
    params: '(url)',
    snippet: '("${1:url}")',
    description: 'Make a GET request',
  },
  post: {
    params: '(url, body)',
    snippet: '("${1:url}", body=${2:data})',
    description: 'Make a POST request',
  },
  delete: {
    params: '(url)',
    snippet: '("${1:url}")',
    description: 'Make a DELETE request',
  },
  head: {
    params: '(url)',
    snippet: '("${1:url}")',
    description: 'Make a HEAD request',
  },
  patch: {
    params: '(url, body)',
    snippet: '("${1:url}", body=${2:data})',
    description: 'Make a PATCH request',
  },

  // gl methods
  trace: {
    params: '(*args)',
    snippet: '(${1:value})',
    description: 'Debug tracing output',
  },
  trace_time_micro: {
    params: '()',
    snippet: '()',
    description: 'Get runtime in microseconds',
  },
  deploy_contract: {
    params: '(contract_cls, *args)',
    snippet: '(${1:ContractClass})',
    description: 'Deploy a new GenVM contract',
  },
  get_contract_at: {
    params: '(contract_cls, address)',
    snippet: '(${1:ContractClass}, ${2:address})',
    description: 'Get contract proxy at address',
  },

  // gl.storage methods
  inmem_allocate: {
    params: '(type)',
    snippet: '(${1:type})',
    description: 'Allocate storage type in memory',
  },
  copy_to_memory: {
    params: '(data)',
    snippet: '(${1:data})',
    description: 'Copy data to memory',
  },

  // gl.advanced methods
  user_error_immediate: {
    params: '(message)',
    snippet: '("${1:error message}")',
    description: 'Raise immediate user error',
  },

  // gl.vm methods
  spawn_sandbox: {
    params: '(fn)',
    snippet: '(${1:fn})',
    description: 'Spawn sandboxed execution',
  },
  run_nondet: {
    params: '(fn)',
    snippet: '(${1:fn})',
    description: 'Run non-deterministic operation',
  },
  run_nondet_unsafe: {
    params: '(fn)',
    snippet: '(${1:fn})',
    description: 'Run unsafe non-deterministic operation',
  },
  unpack_result: {
    params: '(result)',
    snippet: '(${1:result})',
    description: 'Unpack result value',
  },

  // gl.evm methods
  contract_interface: {
    params: '(address)',
    snippet: '(${1:address})',
    description: 'EVM contract interface decorator',
  },
  encode: {
    params: '(data)',
    snippet: '(${1:data})',
    description: 'Encode data to EVM calldata',
  },
  decode: {
    params: '(data)',
    snippet: '(${1:data})',
    description: 'Decode EVM return data',
  },
  selector_of: {
    params: '(name, params)',
    snippet: '("${1:name}", ${2:params})',
    description: 'Get function selector bytes',
  },
  signature_of: {
    params: '(name, params)',
    snippet: '("${1:name}", ${2:params})',
    description: 'Get function signature string',
  },
  type_name_of: {
    params: '(type)',
    snippet: '(${1:type})',
    description: 'Get EVM type name',
  },

  // gl.public methods (decorators, not called directly but may be useful)
  payable: {
    params: '',
    snippet: '',
    description: 'Make method payable',
  },
  min_gas: {
    params: '(leader, validator)',
    snippet: '(${1:100000}, ${2:50000})',
    description: 'Set minimum gas requirements',
  },

  // Contract instance methods (from ContractAt)
  emit: {
    params: '(value?, on?)',
    snippet: '()',
    description: 'Emit a write transaction to the contract',
  },
  view: {
    params: '(state?)',
    snippet: '()',
    description: 'Call view methods on the contract',
  },
  emit_transfer: {
    params: '(value, on?)',
    snippet: '(value=${1:amount})',
    description: 'Transfer value to the contract',
  },

  // Common emit() methods
  send_message: {
    params: '(chain_id, address, message)',
    snippet: '(${1:chain_id}, ${2:address}, ${3:message})',
    description: 'Send cross-chain message',
  },
  transfer: {
    params: '(to, amount)',
    snippet: '(${1:to}, ${2:amount})',
    description: 'Transfer tokens',
  },
  mint: {
    params: '(to, amount)',
    snippet: '(${1:to}, ${2:amount})',
    description: 'Mint new tokens',
  },
  update_storage: {
    params: '(data)',
    snippet: '(${1:data})',
    description: 'Update contract storage',
  },

  // Common view() methods
  get_balance_of: {
    params: '(address)',
    snippet: '(${1:address})',
    description: 'Get balance of address',
  },
  balance_of: {
    params: '(address)',
    snippet: '(${1:address})',
    description: 'Get balance of address',
  },
  total_supply: {
    params: '()',
    snippet: '()',
    description: 'Get total token supply',
  },
} as const satisfies Record<string, MethodSignature>;

// Module descriptions for better context
export const moduleDescriptions = {
  eq_principle: 'Equivalence principle module',
  nondet: 'Non-deterministic operations module',
  message: 'Message context module',
  storage: 'Storage operations module',
  vm: 'Virtual machine operations module',
  advanced: 'Advanced operations module',
  evm: 'EVM compatibility module',
  public: 'Public method decorators',
  web: 'Web operations module',
};

// Class descriptions
export const classDescriptions = {
  Contract: 'Base contract class',
  Event: 'Event definition class',
  UserError: 'User error exception',
  VMError: 'VM error exception',
  Return: 'Return value wrapper',
  Result: 'Result type (union of Return, VMError, UserError)',
  MethodEncoder: 'EVM method encoder for ABI calls',
  ContractProxy: 'EVM contract proxy',
  ContractDeclaration: 'EVM contract declaration',
  bytes32: '32-byte EVM type',
  Root: 'Storage root class',
};

// Property descriptions for various contexts
export const propertyDescriptions = {
  // gl.message properties
  sender: 'Address of message sender (alias for sender_address)',
  sender_address: 'Address of message sender',
  contract_address: 'Address of current contract',
  value: 'Value sent with message',
  chain_id: 'Current chain ID',
  data: 'Message call data',

  // Address properties
  as_hex: 'Get address as hex string',
  as_bytes: 'Get address as bytes',
  as_b64: 'Get address as base64',
  as_int: 'Get address as integer',

  // Contract instance properties
  balance: 'Contract balance (u256)',
  address: 'Contract address',

  // Response properties
  status: 'HTTP status code',
  headers: 'Response headers',
  body: 'Response body content',
};
