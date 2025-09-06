# DXP-516: Return Execution Time - Implementation Plan

## Overview
Add execution time tracking for GenVM operations. Processing times will be stored in transaction receipts, which automatically become part of consensus_data and consensus_history.

## Requirements
- Track GenVM execution time (excluding consensus operations)
- Store processing times for both leader and validator execution in receipts
- Processing time in milliseconds (integer)
- Support both `run_contract` and `get_contract_schema` methods
- No database schema changes required

## Implementation Steps

### 1. Add Processing Time Field to Receipt Dataclass
**File:** `backend/node/types.py`
- Add `processing_time: Optional[int] = None` field to Receipt dataclass (milliseconds)
- Include processing_time in `to_dict()` method
- Include processing_time in `from_dict()` method

### 2. Add Processing Time to ExecutionResult
**File:** `backend/node/genvm/base.py`
- Add `processing_time: int` field to ExecutionResult dataclass (line ~80)
- This allows both `run_contract` and `get_contract_schema` to return timing

### 3. Track Execution Time in GenVMHost Methods
**File:** `backend/node/genvm/base.py`
- In `GenVMHost.run_contract()` method (lines 147-195):
  - Measure time around `_run_genvm_host()` call
  - Use `time.time()` and convert to milliseconds: `int((end_time - start_time) * 1000)`
  - Pass processing_time to ExecutionResult
- In `GenVMHost.get_contract_schema()` method (lines 197-223):
  - Apply same timing logic around `_run_genvm_host()` call
  - Pass processing_time to ExecutionResult

### 4. Pass Execution Time to Receipt in Node Methods
**File:** `backend/node/base.py`
- In `_run_genvm()` method (lines 294-377):
  - Extract `processing_time` from `res` (ExecutionResult)
  - Pass to Receipt constructor (line 353)
- In `get_contract_schema()` method (lines 271-292):
  - Although this returns a schema string, the ExecutionResult now contains timing
  - Can be used for debugging/logging if needed

## Technical Details

### Time Measurement Points
- **Start:** Just before `_run_genvm_host()` call
- **End:** Immediately after `_run_genvm_host()` returns
- **Unit:** Milliseconds (integer)
- **Function:** `time.time()` (sufficient for millisecond precision)
- **Scope:** Total GenVM execution time including any internal retries
- **Excludes:** State preparation, result processing, consensus operations

### Data Flow
1. GenVMHost methods measure execution time around `_run_genvm_host()`
2. Processing time added to ExecutionResult
3. Node._run_genvm() extracts time from ExecutionResult
4. Processing time passed to Receipt constructor
5. Receipt automatically becomes part of consensus_data/consensus_history

## Testing Strategy

### Unit Tests
**File:** `tests/unit/test_node_execution_time.py` (new file)
- Mock GenVM execution with controlled timing
- Verify Receipt contains correct processing_time
- Test both run_contract and get_contract_schema paths
- Test edge cases (zero time, very long execution)
- Verify timing includes retries when GenVM errors occur

## Files to Modify

1. `backend/node/types.py` - Add processing_time to Receipt dataclass
2. `backend/node/genvm/base.py` - Add processing_time to ExecutionResult, measure time in GenVMHost methods
3. `backend/node/base.py` - Extract processing_time from ExecutionResult and pass to Receipt

## Validation Criteria
- [ ] GenVM execution time measured in milliseconds
- [ ] Both run_contract and get_contract_schema track time
- [ ] Processing time included in Receipt objects
- [ ] Time measurement includes any GenVM retries
- [ ] Receipt serialization/deserialization handles processing_time
- [ ] Unit tests pass
- [ ] No impact on existing functionality