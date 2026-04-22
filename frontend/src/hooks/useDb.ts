import type { ContractFile, DeployedContract, TransactionItem } from '@/types';
import Dexie, { type Table } from 'dexie';
import { localnet } from 'genlayer-js/chains';
import { getRuntimeConfigNumber } from '@/utils/runtimeConfig';

/**
 * Chain ID used to backfill existing (pre-v5) records that were created
 * before the frontend knew about multi-network support. Defaults to the
 * build-time `VITE_CHAIN_ID` (operator-configured Studio instance) and
 * falls back to the SDK's localnet chain ID.
 */
function getLegacyChainId(): number {
  return getRuntimeConfigNumber('VITE_CHAIN_ID', localnet.id);
}

class GenLayerSimulatorDB extends Dexie {
  contractFiles!: Table<ContractFile>;
  deployedContracts!: Table<DeployedContract>;
  transactions!: Table<TransactionItem>;

  constructor() {
    super('genLayerSimulatorDB');

    this.version(2).stores({
      contractFiles: 'id',
      deployedContracts: '[contractId+address]',
      transactions:
        '++id, type, status, contractAddress, localContractId, txId',
    });

    this.version(3)
      .stores({
        contractFiles: 'id',
        deployedContracts: '[contractId+address]',
        transactions:
          '++id, type, status, contractAddress, localContractId, hash',
      })
      .upgrade((tx) => {
        return tx
          .table('transactions')
          .toCollection()
          .modify((transaction) => {
            if (transaction.txId && !transaction.hash) {
              transaction.hash = '0x' + transaction.txId;
              delete transaction.txId;
            }
          });
      });

    this.version(4)
      .stores({
        contractFiles: 'id',
        deployedContracts: '[contractId+address]',
        transactions:
          '++id, type, statusName, contractAddress, localContractId, hash',
      })
      .upgrade((tx) => {
        return tx
          .table('transactions')
          .toCollection()
          .modify((transaction) => {
            if (transaction.status && !transaction.statusName) {
              transaction.statusName = transaction.status;
              delete transaction.status;
            }
          });
      });

    this.version(5)
      .stores({
        contractFiles: 'id',
        deployedContracts: '[contractId+address], chainId',
        transactions:
          '++id, type, statusName, contractAddress, localContractId, hash, chainId',
      })
      .upgrade(async (tx) => {
        const chainId = getLegacyChainId();
        await tx
          .table('deployedContracts')
          .toCollection()
          .modify((dc) => {
            if (dc.chainId === undefined) dc.chainId = chainId;
          });
        await tx
          .table('transactions')
          .toCollection()
          .modify((t) => {
            if (t.chainId === undefined) t.chainId = chainId;
          });
      });
  }
}

export const useDb = () => {
  return new GenLayerSimulatorDB();
};
