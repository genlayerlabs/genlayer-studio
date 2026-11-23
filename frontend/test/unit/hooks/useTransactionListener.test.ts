import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useTransactionListener } from '@/hooks/useTransactionListener';
import { useTransactionsStore } from '@/stores';
import { useWebSocketClient } from '@/hooks';

vi.mock('@/stores', () => ({
  useTransactionsStore: vi.fn(),
}));

vi.mock('@/hooks', () => ({
  useWebSocketClient: vi.fn(),
}));

describe('useTransactionListener', () => {
  let transactionsStoreMock: any;
  let webSocketClientMock: any;

  beforeEach(() => {
    transactionsStoreMock = {
      getTransaction: vi.fn(),
      removeTransaction: vi.fn(),
      updateTransaction: vi.fn(),
      transactions: [],
    };

    webSocketClientMock = {
      id: 'mocked-socket-id',
      on: vi.fn(),
    };

    (useTransactionsStore as any).mockReturnValue(transactionsStoreMock);
    (useWebSocketClient as any).mockReturnValue(webSocketClientMock);
  });

  it('should initialize and set up websocket listener', () => {
    const { init } = useTransactionListener();
    init();

    expect(webSocketClientMock.on).toHaveBeenCalledWith(
      'transaction_status_updated',
      expect.any(Function),
    );
  });

  it('should handle transaction status update correctly', async () => {
    const { init } = useTransactionListener();
    init();

    const handleTransactionStatusUpdate =
      webSocketClientMock.on.mock.calls[0][1];

    const eventData = { data: { hash: '123' } };
    const newTx = { hash: '123', status: 'confirmed' };
    transactionsStoreMock.getTransaction.mockResolvedValue(newTx);
    transactionsStoreMock.transactions = [{ hash: '123', status: 'pending' }];

    await handleTransactionStatusUpdate(eventData);

    expect(transactionsStoreMock.updateTransaction).toHaveBeenCalledWith(newTx);
  });

  it('keeps the appeal receipt when an earlier status request finishes later', async () => {
    useTransactionListener().init();
    const onStatus = webSocketClientMock.on.mock.calls[0][1];
    const onAppeal = webSocketClientMock.on.mock.calls.find(
      ([name]: [string]) => name === 'transaction_appeal_updated',
    )[1];
    const event = { data: { hash: '123' } };
    const accepted = { hash: '123', statusName: 'ACCEPTED' };
    const undetermined = { hash: '123', statusName: 'UNDETERMINED' };
    let resolveStatus!: (receipt: typeof undetermined) => void;
    transactionsStoreMock.transactions = [undetermined];
    transactionsStoreMock.getTransaction
      .mockReturnValueOnce(new Promise((resolve) => (resolveStatus = resolve)))
      .mockResolvedValueOnce(accepted);

    const pendingStatus = onStatus(event);
    await onAppeal(event);
    resolveStatus(undetermined);
    await pendingStatus;

    expect(
      transactionsStoreMock.updateTransaction,
    ).toHaveBeenCalledExactlyOnceWith(accepted);
  });

  it('should remove transaction if server tx not found', async () => {
    const { init } = useTransactionListener();
    init();

    const currentTx = { hash: '123', status: 'confirmed' };

    transactionsStoreMock.transactions = [currentTx];

    const handleTransactionStatusUpdate =
      webSocketClientMock.on.mock.calls[0][1];

    const eventData = { data: { hash: '123' } };
    transactionsStoreMock.getTransaction.mockResolvedValue(null);

    await handleTransactionStatusUpdate(eventData);

    expect(transactionsStoreMock.removeTransaction).toHaveBeenCalledWith(
      currentTx,
    );
  });

  it('should do nothing if local transaction is not found', async () => {
    const { init } = useTransactionListener();
    init();

    const handleTransactionStatusUpdate =
      webSocketClientMock.on.mock.calls[0][1];

    const eventData = { data: { hash: '123' } };
    const newTx = { hash: '123', status: 'confirmed' };
    transactionsStoreMock.getTransaction.mockResolvedValue(newTx);
    transactionsStoreMock.transactions = [];

    await handleTransactionStatusUpdate(eventData);

    expect(transactionsStoreMock.updateTransaction).not.toHaveBeenCalled();
  });
});
