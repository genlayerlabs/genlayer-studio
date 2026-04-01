/**
 * Behavioral snapshot tests for useContractListener.
 *
 * Documents the WS-based deploy completion flow:
 * WS 'deployed_contract' event → find matching local tx → register deployed contract.
 * The multi-network refactor must provide a polling alternative when WS is absent.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useContractListener } from '@/hooks/useContractListener';
import { useContractsStore, useTransactionsStore } from '@/stores';
import { useWebSocketClient } from '@/hooks';

vi.mock('@/stores', () => ({
  useContractsStore: vi.fn(),
  useTransactionsStore: vi.fn(),
}));

vi.mock('@/hooks', () => ({
  useWebSocketClient: vi.fn(),
}));

describe('useContractListener — behavioral contract', () => {
  let contractsStoreMock: any;
  let transactionsStoreMock: any;
  let webSocketClientMock: any;

  beforeEach(() => {
    contractsStoreMock = {
      addDeployedContract: vi.fn(),
    };

    transactionsStoreMock = {
      transactions: [],
    };

    webSocketClientMock = {
      on: vi.fn(),
    };

    (useContractsStore as any).mockReturnValue(contractsStoreMock);
    (useTransactionsStore as any).mockReturnValue(transactionsStoreMock);
    (useWebSocketClient as any).mockReturnValue(webSocketClientMock);
  });

  it('should listen for deployed_contract WS event', () => {
    const { init } = useContractListener();
    init();

    expect(webSocketClientMock.on).toHaveBeenCalledWith(
      'deployed_contract',
      expect.any(Function),
    );
  });

  it('should register deployed contract when matching local tx exists', async () => {
    transactionsStoreMock.transactions = [
      {
        hash: '0xDeployHash',
        localContractId: 'my-contract',
        type: 'deploy',
      },
    ];

    const { init } = useContractListener();
    init();

    const handler = webSocketClientMock.on.mock.calls.find(
      (c: any[]) => c[0] === 'deployed_contract',
    )[1];

    await handler({
      transaction_hash: '0xDeployHash',
      data: {
        id: '0xContractAddress',
        data: { state: { accepted: {}, finalized: {} } },
      },
    });

    expect(contractsStoreMock.addDeployedContract).toHaveBeenCalledWith(
      expect.objectContaining({
        contractId: 'my-contract',
        address: '0xContractAddress',
      }),
    );
  });

  it('should NOT register contract when no matching local tx', async () => {
    transactionsStoreMock.transactions = [];

    const { init } = useContractListener();
    init();

    const handler = webSocketClientMock.on.mock.calls.find(
      (c: any[]) => c[0] === 'deployed_contract',
    )[1];

    await handler({
      transaction_hash: '0xUnknownHash',
      data: { id: '0xAddr', data: { state: {} } },
    });

    expect(contractsStoreMock.addDeployedContract).not.toHaveBeenCalled();
  });

  it('should forward defaultState from WS payload', async () => {
    transactionsStoreMock.transactions = [
      { hash: '0xDeploy', localContractId: 'c1', type: 'deploy' },
    ];

    const { init } = useContractListener();
    init();

    const handler = webSocketClientMock.on.mock.calls.find(
      (c: any[]) => c[0] === 'deployed_contract',
    )[1];

    const statePayload = { accepted: { slot: 'data' }, finalized: {} };
    await handler({
      transaction_hash: '0xDeploy',
      data: { id: '0xAddr', data: { state: statePayload } },
    });

    expect(contractsStoreMock.addDeployedContract).toHaveBeenCalledWith(
      expect.objectContaining({ defaultState: statePayload }),
    );
  });

  it('should match tx by transaction_hash field', async () => {
    transactionsStoreMock.transactions = [
      { hash: '0xA', localContractId: 'a' },
      { hash: '0xB', localContractId: 'b' },
    ];

    const { init } = useContractListener();
    init();

    const handler = webSocketClientMock.on.mock.calls.find(
      (c: any[]) => c[0] === 'deployed_contract',
    )[1];

    await handler({
      transaction_hash: '0xB',
      data: { id: '0xAddr', data: { state: {} } },
    });

    expect(contractsStoreMock.addDeployedContract).toHaveBeenCalledWith(
      expect.objectContaining({ contractId: 'b' }),
    );
  });
});
