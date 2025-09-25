import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useContractListener } from '@/hooks/useContractListener';
import { useTransactionsStore, useContractsStore } from '@/stores';
import { useWebSocketClient } from '@/hooks';

vi.mock('@/stores', () => ({
  useTransactionsStore: vi.fn(),
  useContractsStore: vi.fn(),
}));

vi.mock('@/hooks', () => ({
  useWebSocketClient: vi.fn(),
}));

describe('useContractListener', () => {
  let transactionsStoreMock: any;
  let contractsStoreMock: any;
  let webSocketClientMock: any;

  beforeEach(() => {
    transactionsStoreMock = {
      getTransaction: vi.fn(),
      removeTransaction: vi.fn(),
      updateTransaction: vi.fn(),
      transactions: [],
    };

    contractsStoreMock = {
      addDeployedContract: vi.fn(),
      contracts: [],
      deployedContracts: [],
    };

    webSocketClientMock = {
      id: 'mocked-socket-id',
      on: vi.fn(),
    };

    (useTransactionsStore as any).mockReturnValue(transactionsStoreMock);
    (useContractsStore as any).mockReturnValue(contractsStoreMock);
    (useWebSocketClient as any).mockReturnValue(webSocketClientMock);
  });

  it('should initialize and set up websocket listener', () => {
    const { init } = useContractListener();
    init();

    expect(webSocketClientMock.on).toHaveBeenCalledWith(
      'deployed_contract',
      expect.any(Function),
    );
  });

  it('should handle deployed contract event correctly', async () => {
    const { init } = useContractListener();
    init();

    const handleContractDeployed = webSocketClientMock.on.mock.calls.find(
      (call: any) => call[0] === 'deployed_contract',
    )[1];

    const eventData = {
      transaction_hash: '123',
      data: {
        id: 'contract_address',
        data: { state: '{}' },
      },
    };

    transactionsStoreMock.transactions = [
      { hash: '123', localContractId: 'local_contract_id' },
    ];

    await handleContractDeployed(eventData);

    expect(contractsStoreMock.addDeployedContract).toHaveBeenCalledWith({
      contractId: 'local_contract_id',
      address: 'contract_address',
      defaultState: '{}',
    });
  });
});
