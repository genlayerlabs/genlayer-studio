import os
import json
import requests
from database.credentials import get_genlayer_db_connection
from database.types import ConsensusData
from database.functions import DatabaseFunctions
from consensus.utils import vrf, get_contract_state, genvm_url, run_contract

from dotenv import load_dotenv

load_dotenv()


def leader_executes_transaction(
    current_contract_state, transaction_input: dict, leader_config: dict
) -> dict:
    args_str = ", ".join(f"{json.dumps(arg)}" for arg in transaction_input["args"])

    class_name = transaction_input["function_name"].split(".")[0]
    function_name = transaction_input["function_name"].split(".")[1]

    exec_file_for_genvm = run_contract(
        contract_code=current_contract_state["code"],
        encoded_state=str(current_contract_state["state"]),
        run_by="leader",
        function_name=function_name,
        args_str=args_str,
    )

    payload = {
        "jsonrpc": "2.0",
        "method": "leader_executes_transaction",
        "params": [exec_file_for_genvm, leader_config],
        "id": 2,
    }

    response = requests.post(genvm_url() + "/api", json=payload).json()

    if response["result"]["status"] == "error":
        raise Exception(response["result"]["data"])

    # TODO: Figure out how to do this in the GenVM
    result = response["result"]["data"]
    result["class"] = class_name

    return {"vote": "agree", "result": result}


def validator_executes_transaction(
    current_contract_state,
    transaction_input: dict,
    validator_config: dict,
    leader_receipt: dict,
) -> dict:
    args_str = ", ".join(f"{json.dumps(arg)}" for arg in transaction_input["args"])

    class_name = transaction_input["function_name"].split(".")[0]
    function_name = transaction_input["function_name"].split(".")[1]

    exec_file_for_genvm = run_contract(
        contract_code=current_contract_state["code"],
        encoded_state=str(current_contract_state["state"]),
        run_by="validator",
        function_name=function_name,
        args_str=args_str,
    )

    payload = {
        "jsonrpc": "2.0",
        "method": "validator_executes_transaction",
        "params": [exec_file_for_genvm, validator_config, leader_receipt],
        "id": 2,
    }

    response = requests.post(genvm_url() + "/api", json=payload).json()

    if response["result"]["status"] == "error":
        raise Exception(response["result"]["data"])

    # TODO: Figure out how to do this in the GenVM
    result = response["result"]["data"]
    result["class"] = class_name

    return_value = {"vote": "disagree", "result": result}

    if (
        return_value["result"]["contract_state"]
        == leader_receipt["result"]["contract_state"]
    ):
        return_value["vote"] = "agree"

    return return_value


async def exec_transaction(from_account, transaction_input, logger=None):

    with DatabaseFunctions() as dbf:
        all_validators = dbf.all_validators()
        dbf.close()

    # Select validators using VRF
    num_validators = int(os.environ["NUMVALIDATORS"])
    selected_validators = vrf(all_validators, num_validators)

    leader = selected_validators[0]
    remaining_validators = selected_validators[1 : num_validators + 1]
    if logger:
        logger(f"Selected Leader: {leader}...")
        logger(f"Selected Validators: {remaining_validators}...")
        logger(f"Leader {leader} starts contract execution...")

    # Get current contract state to pass it to leader and validators
    current_contract_state = get_contract_state(transaction_input["contract_address"])

    # Leader executes transaction
    leader_receipt = leader_executes_transaction(
        current_contract_state, transaction_input, leader
    )

    votes = {leader["address"]: leader_receipt["vote"]}

    if logger:
        logger(f"Leader {leader} has finished contract execution...")

    valudators_results = []
    for validator in remaining_validators:
        validator_receipt = validator_executes_transaction(
            current_contract_state, transaction_input, validator, leader_receipt
        )
        votes[validator["address"]] = validator_receipt["vote"]
        valudators_results.append(validator_receipt)

    # TODO: check if half of the validators agree with the leader or raise an exception
    if len([vote for vote in votes.values() if vote == "agree"]) < num_validators // 2:
        raise Exception("Consensus not reached")

    # Validators execute transaction
    # loop = asyncio.get_running_loop()
    # validation_tasks = [
    #     loop.create_task(validator_executes_transaction(transaction_input, leader_receipt, validator)) # watch out! as ollama uses GPU resources
    #     for validator in remaining_validators
    # ]
    # validation_results = await asyncio.gather(*validation_tasks)

    # for i in range(len(validation_results)):
    #     votes[f"{validation_results[i]['validator']}"] = validation_results[i]["vote"]

    # Write transaction into DB
    from_address = from_account
    to_address = transaction_input["contract_address"]
    data = json.dumps(
        {"new_contract_state": leader_receipt["result"]["contract_state"]}
    )
    transaction_type = 2
    final = False
    consensus_data = ConsensusData(
        final=final, votes=votes, leader=leader_receipt, validators=valudators_results
    ).model_dump_json()
    connection = get_genlayer_db_connection()
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO transactions (from_address, to_address, data, consensus_data, type, created_at) VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP);",
        (
            from_address,
            to_address,
            data,
            consensus_data,
            transaction_type,
        ),
    )
    # Update current state
    new_contract_data = {}
    new_contract_data["code"] = current_contract_state["code"]
    new_contract_data["state"] = leader_receipt["result"]["contract_state"]
    cursor.execute(
        "UPDATE current_state SET data = %s WHERE id = %s;",
        (
            json.dumps(new_contract_data),
            to_address,
        ),
    )

    connection.commit()
    cursor.close()
    connection.close()

    if logger:
        logger(f"Transaction has been fully executed...")
        logger(f"This is the data produced by the leader:\n\n {leader_receipt}")

    execution_output = {}
    execution_output["leader_data"] = leader_receipt
    execution_output["consensus_data"] = consensus_data
    return execution_output
