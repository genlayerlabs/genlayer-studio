import type { ContractFile, DeployedContract, TransactionItem } from '@/types';
import { type PiniaPluginContext } from 'pinia';
import { useDb, useFileName } from '@/hooks';
import {
  isRecordInScope,
  useNetworkStore,
  type RecordScope,
} from '@/stores/network';
import { toRaw } from 'vue';

const ENABLE_LOGS = false;
const db = useDb();
const { cleanupFileName } = useFileName();

// IndexedDB cannot clone Vue proxies, at any depth.
function toPlain<T>(value: T): T {
  const raw = toRaw(value);
  if (Array.isArray(raw)) {
    return raw.map(toPlain) as T;
  }
  if (raw !== null && typeof raw === 'object') {
    const proto = Object.getPrototypeOf(raw);
    if (proto === Object.prototype || proto === null) {
      return Object.fromEntries(
        Object.entries(raw).map(([key, item]) => [key, toPlain(item)]),
      ) as T;
    }
  }
  return raw;
}

// Rows saved without a chain belong to whichever network is asking.
const deployedContractsInScope = (contractId: string, scope: RecordScope) =>
  db.deployedContracts
    .where('contractId')
    .equals(contractId)
    .filter((row) => isRecordInScope(row, scope));

/**
 * Upserts a deployed contract into the database.
 *
 * @param {DeployedContract} contract - The contract to upsert.
 * @param {RecordScope} scope - The network the contract was deployed on.
 * @return {Promise<void>} A Promise that resolves once the contract is upserted.
 */
const upsertDeployedContract = async (
  contract: DeployedContract,
  scope: RecordScope,
): Promise<void> => {
  const record = toPlain({ ...contract, ...scope });
  const existing = deployedContractsInScope(contract.contractId, scope);
  const sameKey = await db.deployedContracts.get([
    contract.contractId,
    contract.address,
  ]);
  if (sameKey && !isRecordInScope(sameKey, scope)) {
    // The key has no network, so one row without a chain serves every network.
    await existing.delete();
    await db.deployedContracts.put({
      ...record,
      chainId: undefined,
      network: undefined,
    });
    return;
  }
  if ((await existing.count()) > 0) {
    await existing.modify(record);
  } else {
    await db.deployedContracts.add(record);
  }
};

/**
 * A plugin for persisting the state of a Pinia store.
 *
 * @param {PiniaPluginContext} context - The context object containing the Pinia store.
 * @return {void} This function does not return anything.
 */
export function persistStorePlugin(context: PiniaPluginContext): void {
  context.store.$onAction(({ store, name, args, after }) => {
    if (ENABLE_LOGS) {
      console.log(
        `Called Action "${name}" with params [${JSON.stringify(args)}].`,
      );
    }

    after(async (result) => {
      if (store.$id === 'contractsStore') {
        switch (name) {
          case 'addContractFile':
            await db.contractFiles.add({
              ...(args[0] as ContractFile),
              name: cleanupFileName(args[0].name),
            });
            break;
          case 'updateContractFile':
            await db.contractFiles.update(args[0] as string, {
              ...(args[1] as ContractFile),
            });
            break;
          case 'removeContractFile':
            await db.contractFiles.delete(args[0]);
            await db.deployedContracts
              .where('contractId')
              .equals(args[0] as string)
              .delete();
            localStorage.setItem(
              'contractsStore.openedFiles',
              store.openedFiles.join(','),
            );
            break;
          case 'openFile':
            localStorage.setItem(
              'contractsStore.currentContractId',
              args[0] as string,
            );
            localStorage.setItem(
              'contractsStore.openedFiles',
              store.openedFiles.join(','),
            );
            break;
          case 'closeFile':
            localStorage.setItem(
              'contractsStore.currentContractId',
              store.currentContractId,
            );
            localStorage.setItem(
              'contractsStore.openedFiles',
              store.openedFiles.join(','),
            );
            break;
          case 'moveOpenedFile':
            localStorage.setItem(
              'contractsStore.openedFiles',
              store.openedFiles.join(','),
            );
            break;
          case 'addDeployedContract':
            {
              const contract = args[0] as DeployedContract;
              await upsertDeployedContract(
                contract,
                contract.chainId === undefined
                  ? useNetworkStore().recordScope
                  : { chainId: contract.chainId, network: contract.network },
              );
            }
            break;
          case 'setCurrentContractId':
            localStorage.setItem(
              'contractsStore.currentContractId',
              args[0] as string,
            );
            break;

          case 'removeDeployedContract':
            await deployedContractsInScope(
              args[0] as string,
              useNetworkStore().recordScope,
            ).delete();
            break;
          default:
            break;
        }
      } else if (store.$id === 'accountsStore') {
        switch (name) {
          case 'generateNewAccount':
          case 'removeAccount':
          case 'connectExternalWallet':
          case 'disconnectExternalWallet':
          case 'updateExternalWalletAddress':
          case 'setCurrentAccount':
            localStorage.setItem(
              'accountsStore.accounts',
              JSON.stringify(toRaw(store.accounts)),
            );
            localStorage.setItem(
              'accountsStore.currentAccount',
              JSON.stringify(toRaw(store.selectedAccount)),
            );
            break;
          default:
            break;
        }
      } else if (store.$id === 'transactionsStore') {
        const transactions = store.allTransactions as TransactionItem[];
        switch (name) {
          case 'addTransaction':
            await db.transactions.add(
              toPlain(
                transactions.find((t) => t.hash === (args[0] as any).hash) ??
                  args[0],
              ),
            );
            break;
          case 'removeTransaction':
            await db.transactions
              .where('hash')
              .equals((args[0] as any).hash)
              .delete();
            break;
          case 'updateTransaction':
            {
              const hash = (args[0] as any).hash ?? (args[0] as any).txId;
              const transaction = transactions.find((t) => t.hash === hash);
              if (transaction) {
                await db.transactions
                  .where('hash')
                  .equals(transaction.hash)
                  .modify({
                    data: toPlain(transaction.data),
                    statusName: transaction.statusName,
                    addedAt: transaction.addedAt,
                  });
              }
            }
            break;
          case 'clearTransactionsForContract':
            await db.transactions
              .where('localContractId')
              .equals(args[0])
              .delete();
            break;
          default:
            break;
        }
      }

      if (ENABLE_LOGS) {
        console.log('PersistStorePlugin:::', { name, args, result });
      }
    });
  });
}
