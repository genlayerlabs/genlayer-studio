import os
import json

DEFAULT_CONSENSUS_MAIN_ADDRESS = os.environ.get(
    "DEFAULT_CONSENSUS_MAIN_ADDRESS", "0x0000000000000000000000000000000000000000"
)
DEFAULT_CONSENSUS_MAIN_ABI = """[
    {
      "inputs": [],
      "name": "AccessControlBadConfirmation",
      "type": "error"
    },
    {
      "inputs": [
        {
          "internalType": "address",
          "name": "account",
          "type": "address"
        },
        {
          "internalType": "bytes32",
          "name": "neededRole",
          "type": "bytes32"
        }
      ],
      "name": "AccessControlUnauthorizedAccount",
      "type": "error"
    },
    {
      "inputs": [],
      "name": "CallerNotMessages",
      "type": "error"
    },
    {
      "inputs": [],
      "name": "CanNotAppeal",
      "type": "error"
    },
    {
      "inputs": [],
      "name": "EmptyTransaction",
      "type": "error"
    },
    {
      "inputs": [],
      "name": "FinalizationNotAllowed",
      "type": "error"
    },
    {
      "inputs": [],
      "name": "InvalidAddress",
      "type": "error"
    },
    {
      "inputs": [],
      "name": "InvalidGhostContract",
      "type": "error"
    },
    {
      "inputs": [],
      "name": "InvalidInitialization",
      "type": "error"
    },
    {
      "inputs": [],
      "name": "InvalidVote",
      "type": "error"
    },
    {
      "inputs": [],
      "name": "NonGenVMContract",
      "type": "error"
    },
    {
      "inputs": [],
      "name": "NotInitializing",
      "type": "error"
    },
    {
      "inputs": [
        {
          "internalType": "address",
          "name": "owner",
          "type": "address"
        }
      ],
      "name": "OwnableInvalidOwner",
      "type": "error"
    },
    {
      "inputs": [
        {
          "internalType": "address",
          "name": "account",
          "type": "address"
        }
      ],
      "name": "OwnableUnauthorizedAccount",
      "type": "error"
    },
    {
      "inputs": [],
      "name": "ReentrancyGuardReentrantCall",
      "type": "error"
    },
    {
      "inputs": [],
      "name": "TransactionNotAtPendingQueueHead",
      "type": "error"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "appealer",
          "type": "address"
        },
        {
          "indexed": false,
          "internalType": "uint256",
          "name": "appealBond",
          "type": "uint256"
        },
        {
          "indexed": false,
          "internalType": "address[]",
          "name": "appealValidators",
          "type": "address[]"
        }
      ],
      "name": "AppealStarted",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": false,
          "internalType": "address",
          "name": "ghostFactory",
          "type": "address"
        },
        {
          "indexed": false,
          "internalType": "address",
          "name": "genManager",
          "type": "address"
        },
        {
          "indexed": false,
          "internalType": "address",
          "name": "genTransactions",
          "type": "address"
        },
        {
          "indexed": false,
          "internalType": "address",
          "name": "genQueue",
          "type": "address"
        },
        {
          "indexed": false,
          "internalType": "address",
          "name": "genStaking",
          "type": "address"
        },
        {
          "indexed": false,
          "internalType": "address",
          "name": "genMessages",
          "type": "address"
        },
        {
          "indexed": false,
          "internalType": "address",
          "name": "idleness",
          "type": "address"
        },
        {
          "indexed": false,
          "internalType": "address",
          "name": "tribunalAppeal",
          "type": "address"
        }
      ],
      "name": "ExternalContractsSet",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": false,
          "internalType": "uint64",
          "name": "version",
          "type": "uint64"
        }
      ],
      "name": "Initialized",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "recipient",
          "type": "address"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "activator",
          "type": "address"
        }
      ],
      "name": "InternalMessageProcessed",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "recipient",
          "type": "address"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "activator",
          "type": "address"
        }
      ],
      "name": "NewTransaction",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "address",
          "name": "previousOwner",
          "type": "address"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "newOwner",
          "type": "address"
        }
      ],
      "name": "OwnershipTransferStarted",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "address",
          "name": "previousOwner",
          "type": "address"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "newOwner",
          "type": "address"
        }
      ],
      "name": "OwnershipTransferred",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "role",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "previousAdminRole",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "newAdminRole",
          "type": "bytes32"
        }
      ],
      "name": "RoleAdminChanged",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "role",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "account",
          "type": "address"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "sender",
          "type": "address"
        }
      ],
      "name": "RoleGranted",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "role",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "account",
          "type": "address"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "sender",
          "type": "address"
        }
      ],
      "name": "RoleRevoked",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "sender",
          "type": "address"
        }
      ],
      "name": "SlashAppealSubmitted",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "tx_id",
          "type": "bytes32"
        }
      ],
      "name": "TransactionAccepted",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "leader",
          "type": "address"
        }
      ],
      "name": "TransactionActivated",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "sender",
          "type": "address"
        }
      ],
      "name": "TransactionCancelled",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "tx_id",
          "type": "bytes32"
        }
      ],
      "name": "TransactionFinalized",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "oldValidator",
          "type": "address"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "newValidator",
          "type": "address"
        }
      ],
      "name": "TransactionIdleValidatorReplaced",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "uint256",
          "name": "validatorIndex",
          "type": "uint256"
        }
      ],
      "name": "TransactionIdleValidatorReplacementFailed",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "newLeader",
          "type": "address"
        }
      ],
      "name": "TransactionLeaderRotated",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "tx_id",
          "type": "bytes32"
        }
      ],
      "name": "TransactionLeaderTimeout",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": false,
          "internalType": "bytes32[]",
          "name": "tx_ids",
          "type": "bytes32[]"
        }
      ],
      "name": "TransactionNeedsRecomputation",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "tx_id",
          "type": "bytes32"
        },
        {
          "indexed": false,
          "internalType": "address[]",
          "name": "validators",
          "type": "address[]"
        }
      ],
      "name": "TransactionReceiptProposed",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "tx_id",
          "type": "bytes32"
        }
      ],
      "name": "TransactionUndetermined",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "validator",
          "type": "address"
        },
        {
          "indexed": false,
          "internalType": "bool",
          "name": "isLastVote",
          "type": "bool"
        }
      ],
      "name": "TribunalAppealVoteCommitted",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "validator",
          "type": "address"
        },
        {
          "indexed": false,
          "internalType": "bool",
          "name": "isLastVote",
          "type": "bool"
        }
      ],
      "name": "TribunalAppealVoteRevealed",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "validator",
          "type": "address"
        },
        {
          "indexed": false,
          "internalType": "bool",
          "name": "isLastVote",
          "type": "bool"
        }
      ],
      "name": "VoteCommitted",
      "type": "event"
    },
    {
      "anonymous": false,
      "inputs": [
        {
          "indexed": true,
          "internalType": "bytes32",
          "name": "txId",
          "type": "bytes32"
        },
        {
          "indexed": true,
          "internalType": "address",
          "name": "validator",
          "type": "address"
        },
        {
          "indexed": false,
          "internalType": "enum ITransactions.VoteType",
          "name": "voteType",
          "type": "uint8"
        },
        {
          "indexed": false,
          "internalType": "bool",
          "name": "isLastVote",
          "type": "bool"
        },
        {
          "indexed": false,
          "internalType": "enum ITransactions.ResultType",
          "name": "result",
          "type": "uint8"
        }
      ],
      "name": "VoteRevealed",
      "type": "event"
    },
    {
      "inputs": [],
      "name": "DEFAULT_ADMIN_ROLE",
      "outputs": [
        {
          "internalType": "bytes32",
          "name": "",
          "type": "bytes32"
        }
      ],
      "stateMutability": "view",
      "type": "function"
    },
    {
      "inputs": [],
      "name": "acceptOwnership",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "_txId",
          "type": "bytes32"
        },
        {
          "internalType": "bytes",
          "name": "_vrfProof",
          "type": "bytes"
        }
      ],
      "name": "activateTransaction",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "address",
          "name": "_sender",
          "type": "address"
        },
        {
          "internalType": "address",
          "name": "_recipient",
          "type": "address"
        },
        {
          "internalType": "uint256",
          "name": "_numOfInitialValidators",
          "type": "uint256"
        },
        {
          "internalType": "uint256",
          "name": "_maxRotations",
          "type": "uint256"
        },
        {
          "internalType": "bytes",
          "name": "_txData",
          "type": "bytes"
        }
      ],
      "name": "addTransaction",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "_txId",
          "type": "bytes32"
        }
      ],
      "name": "cancelTransaction",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "_txId",
          "type": "bytes32"
        },
        {
          "internalType": "bytes32",
          "name": "_commitHash",
          "type": "bytes32"
        }
      ],
      "name": "commitTribunalAppealVote",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "_txId",
          "type": "bytes32"
        },
        {
          "internalType": "bytes32",
          "name": "_commitHash",
          "type": "bytes32"
        }
      ],
      "name": "commitVote",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [],
      "name": "contracts",
      "outputs": [
        {
          "internalType": "contract IGenManager",
          "name": "genManager",
          "type": "address"
        },
        {
          "internalType": "contract ITransactions",
          "name": "genTransactions",
          "type": "address"
        },
        {
          "internalType": "contract IQueues",
          "name": "genQueue",
          "type": "address"
        },
        {
          "internalType": "contract IGhostFactory",
          "name": "ghostFactory",
          "type": "address"
        },
        {
          "internalType": "contract IGenStaking",
          "name": "genStaking",
          "type": "address"
        },
        {
          "internalType": "contract IMessages",
          "name": "genMessages",
          "type": "address"
        },
        {
          "internalType": "contract IIdleness",
          "name": "idleness",
          "type": "address"
        },
        {
          "internalType": "contract ITribunalAppeal",
          "name": "tribunalAppeal",
          "type": "address"
        }
      ],
      "stateMutability": "view",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "address",
          "name": "_recipient",
          "type": "address"
        },
        {
          "internalType": "uint256",
          "name": "_value",
          "type": "uint256"
        },
        {
          "internalType": "bytes",
          "name": "_data",
          "type": "bytes"
        }
      ],
      "name": "executeMessage",
      "outputs": [
        {
          "internalType": "bool",
          "name": "success",
          "type": "bool"
        }
      ],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "_txId",
          "type": "bytes32"
        }
      ],
      "name": "finalizeTransaction",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [],
      "name": "getContracts",
      "outputs": [
        {
          "components": [
            {
              "internalType": "contract IGenManager",
              "name": "genManager",
              "type": "address"
            },
            {
              "internalType": "contract ITransactions",
              "name": "genTransactions",
              "type": "address"
            },
            {
              "internalType": "contract IQueues",
              "name": "genQueue",
              "type": "address"
            },
            {
              "internalType": "contract IGhostFactory",
              "name": "ghostFactory",
              "type": "address"
            },
            {
              "internalType": "contract IGenStaking",
              "name": "genStaking",
              "type": "address"
            },
            {
              "internalType": "contract IMessages",
              "name": "genMessages",
              "type": "address"
            },
            {
              "internalType": "contract IIdleness",
              "name": "idleness",
              "type": "address"
            },
            {
              "internalType": "contract ITribunalAppeal",
              "name": "tribunalAppeal",
              "type": "address"
            }
          ],
          "internalType": "struct IConsensusMain.ExternalContracts",
          "name": "",
          "type": "tuple"
        }
      ],
      "stateMutability": "view",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "role",
          "type": "bytes32"
        }
      ],
      "name": "getRoleAdmin",
      "outputs": [
        {
          "internalType": "bytes32",
          "name": "",
          "type": "bytes32"
        }
      ],
      "stateMutability": "view",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "address",
          "name": "addr",
          "type": "address"
        }
      ],
      "name": "ghostContracts",
      "outputs": [
        {
          "internalType": "bool",
          "name": "isGhost",
          "type": "bool"
        }
      ],
      "stateMutability": "view",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "role",
          "type": "bytes32"
        },
        {
          "internalType": "address",
          "name": "account",
          "type": "address"
        }
      ],
      "name": "grantRole",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "role",
          "type": "bytes32"
        },
        {
          "internalType": "address",
          "name": "account",
          "type": "address"
        }
      ],
      "name": "hasRole",
      "outputs": [
        {
          "internalType": "bool",
          "name": "",
          "type": "bool"
        }
      ],
      "stateMutability": "view",
      "type": "function"
    },
    {
      "inputs": [],
      "name": "initialize",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [],
      "name": "owner",
      "outputs": [
        {
          "internalType": "address",
          "name": "",
          "type": "address"
        }
      ],
      "stateMutability": "view",
      "type": "function"
    },
    {
      "inputs": [],
      "name": "pendingOwner",
      "outputs": [
        {
          "internalType": "address",
          "name": "",
          "type": "address"
        }
      ],
      "stateMutability": "view",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "_txId",
          "type": "bytes32"
        },
        {
          "internalType": "bytes",
          "name": "_txReceipt",
          "type": "bytes"
        },
        {
          "internalType": "uint256",
          "name": "_processingBlock",
          "type": "uint256"
        },
        {
          "components": [
            {
              "internalType": "enum IMessages.MessageType",
              "name": "messageType",
              "type": "uint8"
            },
            {
              "internalType": "address",
              "name": "recipient",
              "type": "address"
            },
            {
              "internalType": "uint256",
              "name": "value",
              "type": "uint256"
            },
            {
              "internalType": "bytes",
              "name": "data",
              "type": "bytes"
            },
            {
              "internalType": "bool",
              "name": "onAcceptance",
              "type": "bool"
            }
          ],
          "internalType": "struct IMessages.SubmittedMessage[]",
          "name": "_messages",
          "type": "tuple[]"
        },
        {
          "internalType": "bytes",
          "name": "_vrfProof",
          "type": "bytes"
        }
      ],
      "name": "proposeReceipt",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [],
      "name": "renounceOwnership",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "role",
          "type": "bytes32"
        },
        {
          "internalType": "address",
          "name": "callerConfirmation",
          "type": "address"
        }
      ],
      "name": "renounceRole",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "_txId",
          "type": "bytes32"
        },
        {
          "internalType": "bytes32",
          "name": "_voteHash",
          "type": "bytes32"
        },
        {
          "internalType": "enum ITribunalAppeal.TribunalVoteType",
          "name": "_voteType",
          "type": "uint8"
        },
        {
          "internalType": "uint256",
          "name": "_nonce",
          "type": "uint256"
        }
      ],
      "name": "revealTribunalAppealVote",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "_txId",
          "type": "bytes32"
        },
        {
          "internalType": "bytes32",
          "name": "_voteHash",
          "type": "bytes32"
        },
        {
          "internalType": "enum ITransactions.VoteType",
          "name": "_voteType",
          "type": "uint8"
        },
        {
          "internalType": "uint256",
          "name": "_nonce",
          "type": "uint256"
        }
      ],
      "name": "revealVote",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "role",
          "type": "bytes32"
        },
        {
          "internalType": "address",
          "name": "account",
          "type": "address"
        }
      ],
      "name": "revokeRole",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "address",
          "name": "_ghostFactory",
          "type": "address"
        },
        {
          "internalType": "address",
          "name": "_genManager",
          "type": "address"
        },
        {
          "internalType": "address",
          "name": "_genTransactions",
          "type": "address"
        },
        {
          "internalType": "address",
          "name": "_genQueue",
          "type": "address"
        },
        {
          "internalType": "address",
          "name": "_genStaking",
          "type": "address"
        },
        {
          "internalType": "address",
          "name": "_genMessages",
          "type": "address"
        },
        {
          "internalType": "address",
          "name": "_idleness",
          "type": "address"
        },
        {
          "internalType": "address",
          "name": "_tribunalAppeal",
          "type": "address"
        }
      ],
      "name": "setExternalContracts",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "_txId",
          "type": "bytes32"
        }
      ],
      "name": "submitAppeal",
      "outputs": [],
      "stateMutability": "payable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes32",
          "name": "_txId",
          "type": "bytes32"
        }
      ],
      "name": "submitSlashAppeal",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "bytes4",
          "name": "interfaceId",
          "type": "bytes4"
        }
      ],
      "name": "supportsInterface",
      "outputs": [
        {
          "internalType": "bool",
          "name": "",
          "type": "bool"
        }
      ],
      "stateMutability": "view",
      "type": "function"
    },
    {
      "inputs": [
        {
          "internalType": "address",
          "name": "newOwner",
          "type": "address"
        }
      ],
      "name": "transferOwnership",
      "outputs": [],
      "stateMutability": "nonpayable",
      "type": "function"
    }
]"""


_FEES_DISTRIBUTION_COMPONENTS = [
    {"internalType": "uint256", "name": "leaderTimeunitsAllocation", "type": "uint256"},
    {
        "internalType": "uint256",
        "name": "validatorTimeunitsAllocation",
        "type": "uint256",
    },
    {"internalType": "uint256", "name": "appealRounds", "type": "uint256"},
    {"internalType": "uint256", "name": "executionBudgetPerRound", "type": "uint256"},
    {"internalType": "uint256", "name": "executionConsumed", "type": "uint256"},
    {"internalType": "uint256", "name": "totalMessageFees", "type": "uint256"},
    {"internalType": "uint256[]", "name": "rotations", "type": "uint256[]"},
    {"internalType": "uint256", "name": "maxPriceGenPerTimeUnit", "type": "uint256"},
    {"internalType": "uint256", "name": "storageFeeMaxGasPrice", "type": "uint256"},
    {"internalType": "uint256", "name": "receiptFeeMaxGasPrice", "type": "uint256"},
]

_MESSAGE_ALLOCATION_COMPONENTS = [
    {
        "internalType": "enum IMessages.MessageType",
        "name": "messageType",
        "type": "uint8",
    },
    {"internalType": "bool", "name": "onAcceptance", "type": "bool"},
    {"internalType": "uint256", "name": "parentIndex", "type": "uint256"},
    {"internalType": "address", "name": "recipient", "type": "address"},
    {"internalType": "bytes32", "name": "callKey", "type": "bytes32"},
    {"internalType": "uint256", "name": "budget", "type": "uint256"},
    {"internalType": "bytes", "name": "feeParams", "type": "bytes"},
]

_ADD_TRANSACTION_PARAMS_COMPONENTS = [
    {"internalType": "address", "name": "sender", "type": "address"},
    {"internalType": "address", "name": "recipient", "type": "address"},
    {"internalType": "uint256", "name": "numOfInitialValidators", "type": "uint256"},
    {"internalType": "uint256", "name": "maxRotations", "type": "uint256"},
    {"internalType": "uint256", "name": "validUntil", "type": "uint256"},
    {"internalType": "uint256", "name": "saltNonce", "type": "uint256"},
    {"internalType": "uint256", "name": "userValue", "type": "uint256"},
    {
        "components": _FEES_DISTRIBUTION_COMPONENTS,
        "internalType": "struct IFeeManager.FeesDistribution",
        "name": "feesDistribution",
        "type": "tuple",
    },
    {"internalType": "bytes", "name": "txCalldata", "type": "bytes"},
    {
        "components": _MESSAGE_ALLOCATION_COMPONENTS,
        "internalType": "struct IMessages.MessageFeeAllocationNode[]",
        "name": "messageAllocations",
        "type": "tuple[]",
    },
]

_STUDIO_TRAIN_TRANSACTION_ABI = [
    {
        "inputs": [
            {
                "components": _ADD_TRANSACTION_PARAMS_COMPONENTS,
                "internalType": "struct IConsensusMainWithFees.AddTransactionParams",
                "name": "_params",
                "type": "tuple",
            }
        ],
        "name": function_name,
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    }
    for function_name in ("addTransaction", "deploySalted")
] + [
    {
        "inputs": [
            {"internalType": "bytes32", "name": "_txId", "type": "bytes32"},
            {
                "components": _FEES_DISTRIBUTION_COMPONENTS,
                "internalType": "struct IFeeManager.FeesDistribution",
                "name": "_feesDistribution",
                "type": "tuple",
            },
        ],
        "name": "topUpFees",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "_txId", "type": "bytes32"},
            {
                "internalType": "uint256",
                "name": "_expectedDecisionId",
                "type": "uint256",
            },
            {
                "components": _FEES_DISTRIBUTION_COMPONENTS,
                "internalType": "struct IFeeManager.FeesDistribution",
                "name": "_feesDistribution",
                "type": "tuple",
            },
        ],
        "name": "topUpAndSubmitAppeal",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "_txId", "type": "bytes32"},
            {
                "internalType": "uint256",
                "name": "_expectedDecisionId",
                "type": "uint256",
            },
        ],
        "name": "submitAppeal",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "_txId", "type": "bytes32"},
            {
                "internalType": "uint256",
                "name": "_expectedDecisionId",
                "type": "uint256",
            },
        ],
        "name": "finalizeTransaction",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


def _abi_function_signature(entry: dict) -> str:
    """Canonical ``name(type,...)`` signature, i.e. the 4-byte selector identity."""

    def canonical(component: dict) -> str:
        component_type = component["type"]
        if not component_type.startswith("tuple"):
            return component_type
        inner = ",".join(
            canonical(nested) for nested in component.get("components", [])
        )
        return f"({inner}){component_type[len('tuple'):]}"

    inputs = ",".join(canonical(entry_input) for entry_input in entry.get("inputs", []))
    return f"{entry['name']}({inputs})"


# Names kept at their pre-fee signature on the *served* surface.
#
# This ABI has two consumers with opposite requirements:
#
#   * Decoding. TransactionParser matches an incoming selector against
#     this ABI plus the FEE_AWARE_* overloads it appends itself, so it wants
#     every generation of every entrypoint. That union lives in
#     TransactionParser._get_contract_abi, not here.
#   * Serving. sim_getConsensusContract hands this ABI to clients, and
#     genlayer-py resolves entrypoints with web3's get_function_by_name(),
#     which raises Web3ValueError on an overloaded name. The served surface
#     must therefore be name-unique, and each shared name must carry the
#     generation its callers actually encode.
#
# addTransaction is the one name where those callers disagree. Every client
# that reaches Studio through sim_getConsensusContract — released genlayer-py
# (so gltest and the load tests) and the train genlayer-py alike — builds five
# positional arguments and encodes them against contract_fn.argument_types.
# The single-tuple fee-aware form makes that raise, and it is also not a
# function Studio's deployed ConsensusMain.sol exposes. Train clients detect
# the pre-fee shape and fall back to the same five-argument path, so serving
# it satisfies both generations; the fee-aware form stays decodable through
# FEE_AWARE_ADD_TRANSACTION_ABI.
#
# submitAppeal and finalizeTransaction keep their v0.6 decision-bound form:
# no Studio client encodes them from the served ABI (genlayer-js embeds its
# own train ABI, and released genlayer-py's appeal path is unreachable here),
# while the train genlayer-py requires the DecisionId argument.
_SERVED_PRE_FEE_FUNCTION_NAMES = frozenset({"addTransaction"})


def get_default_consensus_main_contract():
    abi = json.loads(DEFAULT_CONSENSUS_MAIN_ABI)
    served_train_abi = [
        entry
        for entry in _STUDIO_TRAIN_TRANSACTION_ABI
        if entry["name"] not in _SERVED_PRE_FEE_FUNCTION_NAMES
    ]
    served_train_names = {entry["name"] for entry in served_train_abi}
    abi = [
        entry
        for entry in abi
        if entry.get("type") != "function"
        or entry.get("name") not in served_train_names
    ]
    abi.extend(served_train_abi)
    return {
        "address": DEFAULT_CONSENSUS_MAIN_ADDRESS,
        "abi": abi,
        "bytecode": "0x",
    }
