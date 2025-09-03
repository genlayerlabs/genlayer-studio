import type { ContractFile, DeployedContract, ImportedContract, TransactionItem } from '@/types';
import Dexie, { type Table } from 'dexie';

class GenLayerSimulatorDB extends Dexie {
  contractFiles!: Table<ContractFile>;
  deployedContracts!: Table<DeployedContract>;
  importedContracts!: Table<ImportedContract>;
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

    this.version(5).stores({
      contractFiles: 'id',
      deployedContracts: '[contractId+address]',
      importedContracts: 'id, address, name, importedAt',
      transactions:
        '++id, type, statusName, contractAddress, localContractId, hash',
    });
  }
}

export const useDb = () => {
  return new GenLayerSimulatorDB();
};
