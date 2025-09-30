# GenVM Autocomplete Testing Guide

## How to Test the Autocomplete Feature

1. Open the Studio frontend at http://localhost:8080
2. Navigate to the Simulator and open or create a contract
3. Test the following autocomplete scenarios:

### Test Cases

#### 1. Root gl completions
- Type `gl.` and verify you see:
  - Modules: eq_principle, nondet, message, storage, vm, advanced, evm, public
  - Classes: Contract, Event
  - Methods: ContractAt, trace, trace_time_micro, deploy_contract, get_contract_at

#### 2. Module completions
- Type `gl.nondet.` → should show: web, exec_prompt
- Type `gl.nondet.web.` → should show: render, request, get, post, delete, head, patch
- Type `gl.message.` → should show: sender, sender_address, contract_address, value, chain_id, data
- Type `gl.storage.` → should show: inmem_allocate, copy_to_memory, Root
- Type `gl.vm.` → should show: UserError, VMError, Return, Result, spawn_sandbox, run_nondet, etc.
- Type `gl.public.` → should show: view, write
- Type `gl.public.write.` → should show: payable, min_gas

#### 3. Method signatures with snippets
- Type `gl.ContractAt` and accept completion → should insert `gl.ContractAt(${1:address})`
- Type `gl.nondet.web.post` and accept → should insert `gl.nondet.web.post("${1:url}", body=${2:data})`
- Type `gl.trace` and accept → should insert `gl.trace(${1:value})`

#### 4. Address constructor
- Type `Address` → should show Address() constructor
- Accept completion → should insert `Address("${1:0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6}")`

#### 5. Contract instance completions
After typing something like:
```python
contract = gl.ContractAt(...)
```
- Type `contract.` → should show: emit, view, emit_transfer, balance, address

#### 6. Chained method completions
- Type `contract.emit().` → should show: send_message, transfer, mint, update_storage, foo, bar, test
- Type `contract.view().` → should show: get_balance_of, balance_of, total_supply, get_name, etc.

#### 7. Address property completions
For a variable that contains 'address' in its name:
- Type `my_address.` → should show: as_hex, as_bytes, as_b64, as_int

## Expected Behavior
- Completions should appear automatically after typing trigger characters (`.`, `g`, `l`, `A`)
- Snippets should have tab stops for parameter placeholders
- Method completions should show parameter hints
- Descriptions should be visible in the completion popup

## Success Criteria
✅ All gl.* API completions work correctly
✅ Method signatures include parameter snippets
✅ Context-aware completions (contract instances, addresses)
✅ Trigger characters activate completions
✅ No TypeScript errors in the console