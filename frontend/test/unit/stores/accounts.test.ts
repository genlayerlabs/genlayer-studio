import { describe, it, expect, beforeEach, vi, type Mock } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useAccountsStore, type AccountInfo } from '@/stores';
import { useGenlayer } from '@/hooks';
import type { Address } from 'genlayer-js/types';

const testKey1 =
  '0xb69426b0f5838a514b263868978faaa53057ac83c5ccad6b7fddbc051b052c6a' as Address; // ! NEVER USE THIS PRIVATE KEY
const testAddress1 = '0x0200E9994260fe8D40107E01101F807B2e7A29Da' as Address;
const testKey2 =
  '0x483b7a9b979289a227095c22229028a5debe04d6d1c8434d8bd5b48f78544263' as Address; // ! NEVER USE THIS PRIVATE KEY

let mockWebSocketClientGlobal: any = {
  connected: true,
  emit: vi.fn(),
  on: vi.fn(),
  off: vi.fn(),
};

vi.mock('@/hooks', () => ({
  useGenlayer: vi.fn(),
  useShortAddress: vi.fn(() => ({})),
  useWebSocketClient: vi.fn(() => mockWebSocketClientGlobal),
}));

vi.mock('genlayer-js', () => ({
  createAccount: vi.fn(() => ({ address: testAddress1 })),
  generatePrivateKey: vi.fn(() => testKey1),
}));

describe('useAccountsStore', () => {
  let accountsStore: ReturnType<typeof useAccountsStore>;
  let mockWebSocketClient: any;
  const mockGenlayerClient = {
    getTransaction: vi.fn(),
  };

  beforeEach(() => {
    // Set up mocks BEFORE creating the store
    mockWebSocketClient = {
      connected: true,
      emit: vi.fn(),
      on: vi.fn(),
      off: vi.fn(),
    };
    mockWebSocketClientGlobal = mockWebSocketClient;

    setActivePinia(createPinia());
    (useGenlayer as Mock).mockReturnValue({
      client: mockGenlayerClient,
    });

    // Mock localStorage
    vi.stubGlobal('localStorage', {
      getItem: vi.fn(),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });

    // Clear mocks before creating store
    mockGenlayerClient.getTransaction.mockClear();
    mockWebSocketClient.emit.mockClear();
    mockWebSocketClient.on.mockClear();
    mockWebSocketClient.off.mockClear();

    // Now create the store - this will trigger the WebSocket setup
    accountsStore = useAccountsStore();

    (localStorage.getItem as Mock).mockClear();
    (localStorage.setItem as Mock).mockClear();
    (localStorage.removeItem as Mock).mockClear();
  });

  it('should generate a new account', () => {
    const newAccount = accountsStore.generateNewAccount();

    expect(newAccount).toEqual({
      type: 'local',
      address: testAddress1,
      privateKey: testKey1,
    });
    expect(accountsStore.accounts).toContainEqual(newAccount);
    expect(accountsStore.selectedAccount).toEqual(newAccount);
  });

  it('should remove an account and default to existing one', () => {
    const account1 = {
      type: 'local' as const,
      address: testAddress1,
      privateKey: testKey1,
    } as AccountInfo;
    const account2 = {
      type: 'local' as const,
      address: '0x456' as Address,
      privateKey: testKey2,
    } as AccountInfo;
    accountsStore.accounts = [account1, account2];
    accountsStore.selectedAccount = account1;

    accountsStore.removeAccount(account1);

    expect(accountsStore.accounts).toEqual([account2]);
    expect(accountsStore.selectedAccount).toEqual(account2);
  });

  it('should throw error when removing the last local account', () => {
    const account1 = {
      type: 'local' as const,
      address: testAddress1,
      privateKey: testKey1,
    } as AccountInfo;
    accountsStore.accounts = [account1];

    expect(() => accountsStore.removeAccount(account1)).toThrow(
      'You need at least 1 local account',
    );
  });

  it('should handle errors in displayAddress computation', () => {
    const invalidAccount = {
      type: 'local' as const,
      address: '0xinvalid' as Address,
      privateKey: '0xinvalidkey' as Address,
    } as AccountInfo;
    accountsStore.selectedAccount = invalidAccount;

    const consoleSpy = vi.spyOn(console, 'error');
    consoleSpy.mockImplementation(() => {});

    expect(accountsStore.displayAddress).toBe('0x');

    consoleSpy.mockRestore();
  });

  it('should set current account', () => {
    const account2 = {
      type: 'local' as const,
      address: '0x456' as Address,
      privateKey: testKey2,
    } as AccountInfo;
    accountsStore.setCurrentAccount(account2);

    expect(accountsStore.selectedAccount).toEqual(account2);
  });

  it('should compute currentUserAddress correctly', () => {
    const account1 = {
      type: 'local' as const,
      address: testAddress1,
      privateKey: testKey1,
    } as AccountInfo;
    accountsStore.selectedAccount = account1;

    expect(accountsStore.currentUserAddress).toBe(testAddress1);
  });

  it('should return an empty string for currentUserAddress when no account is selected', () => {
    accountsStore.selectedAccount = null;
    expect(accountsStore.currentUserAddress).toBe('');
  });

  describe('WebSocket reconnection', () => {
    it('should set up connect event handler on store initialization with off/on pattern', () => {
      expect(mockWebSocketClient.off).toHaveBeenCalledWith(
        'connect',
        expect.any(Function),
      );
      expect(mockWebSocketClient.on).toHaveBeenCalledWith(
        'connect',
        expect.any(Function),
      );
    });

    it('should resubscribe to account on WebSocket connect if there is a current subscription', () => {
      const testAccount = {
        type: 'local' as const,
        address: testAddress1,
        privateKey: testKey1,
      };

      // Set current account to trigger subscription
      accountsStore.setCurrentAccount(testAccount);
      mockWebSocketClient.emit.mockClear();

      // Simulate WebSocket connect event
      const connectHandler = mockWebSocketClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'connect',
      )?.[1];

      if (connectHandler) {
        connectHandler();
      }

      expect(mockWebSocketClient.emit).toHaveBeenCalledWith('subscribe', [
        testAddress1,
      ]);
    });

    it('should not resubscribe on WebSocket connect if current account is set to null', () => {
      // Set current account to null to simulate no active subscription
      accountsStore.setCurrentAccount(null);
      mockWebSocketClient.emit.mockClear();

      // Simulate WebSocket connect event
      const connectHandler = mockWebSocketClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'connect',
      )?.[1];

      if (connectHandler) {
        connectHandler();
      }

      expect(mockWebSocketClient.emit).not.toHaveBeenCalledWith(
        'subscribe',
        expect.anything(),
      );
    });

    it('should subscribe to account when setting current account and WebSocket is connected', () => {
      mockWebSocketClient.connected = true;

      // First set to null to clear any existing subscription
      accountsStore.setCurrentAccount(null);
      mockWebSocketClient.emit.mockClear();

      const testAccount = {
        type: 'local' as const,
        address: testAddress1,
        privateKey: testKey1,
      };

      accountsStore.setCurrentAccount(testAccount);

      expect(mockWebSocketClient.emit).toHaveBeenCalledWith('subscribe', [
        testAddress1,
      ]);
    });

    it('should not immediately subscribe when setting account and WebSocket is not connected', () => {
      mockWebSocketClient.connected = false;
      mockWebSocketClient.emit.mockClear();

      const testAccount = {
        type: 'local' as const,
        address: testAddress1,
        privateKey: testKey1,
      };

      accountsStore.setCurrentAccount(testAccount);

      expect(mockWebSocketClient.emit).not.toHaveBeenCalledWith(
        'subscribe',
        expect.anything(),
      );
    });

    it('should avoid duplicate subscriptions when setting the same account twice', () => {
      mockWebSocketClient.connected = true;

      const testAccount = {
        type: 'local' as const,
        address: testAddress1,
        privateKey: testKey1,
      };

      // Set account first time
      accountsStore.setCurrentAccount(testAccount);
      const firstCallCount = mockWebSocketClient.emit.mock.calls.filter(
        (call: any) => call[0] === 'subscribe',
      ).length;

      mockWebSocketClient.emit.mockClear();

      // Set same account again - should not trigger another subscription
      accountsStore.setCurrentAccount(testAccount);

      expect(mockWebSocketClient.emit).not.toHaveBeenCalledWith(
        'subscribe',
        expect.anything(),
      );
      expect(mockWebSocketClient.emit).not.toHaveBeenCalledWith(
        'unsubscribe',
        expect.anything(),
      );
    });

    it('should unsubscribe from old account when switching accounts', () => {
      mockWebSocketClient.connected = true;

      const account1 = {
        type: 'local' as const,
        address: testAddress1,
        privateKey: testKey1,
      };

      const account2 = {
        type: 'local' as const,
        address: '0x456' as Address,
        privateKey: testKey2,
      };

      // Set first account
      accountsStore.setCurrentAccount(account1);
      mockWebSocketClient.emit.mockClear();

      // Switch to second account
      accountsStore.setCurrentAccount(account2);

      expect(mockWebSocketClient.emit).toHaveBeenCalledWith('unsubscribe', [
        testAddress1,
      ]);
      expect(mockWebSocketClient.emit).toHaveBeenCalledWith('subscribe', [
        '0x456',
      ]);
    });
  });
});

describe('fetchMetaMaskAccount', () => {
  let accountsStore: ReturnType<typeof useAccountsStore>;

  beforeEach(() => {
    setActivePinia(createPinia());
    accountsStore = useAccountsStore();

    // Mock `window.ethereum`
    vi.stubGlobal('window', {
      ethereum: {
        request: vi.fn(),
        on: vi.fn(),
      },
    });
  });

  it('should fetch the MetaMask account and set it as selected', async () => {
    const testAccount = '0x1234567890abcdef1234567890abcdef12345678';
    (window?.ethereum?.request as Mock).mockResolvedValueOnce([testAccount]);

    await accountsStore.fetchMetaMaskAccount();

    expect(window?.ethereum?.request).toHaveBeenCalledWith({
      method: 'eth_requestAccounts',
    });

    const expectedMetaMaskAccount = {
      type: 'metamask',
      address: '0x1234567890AbcdEF1234567890aBcdef12345678', // Checksum format from getAddress
    };

    expect(accountsStore.accounts).toContainEqual(expectedMetaMaskAccount);
    expect(accountsStore.selectedAccount).toEqual(expectedMetaMaskAccount);
  });

  it('should not modify accounts if window.ethereum is undefined', async () => {
    vi.stubGlobal('window', {}); // Remove ethereum from window object
    const initialAccounts = [...accountsStore.accounts];

    await accountsStore.fetchMetaMaskAccount();

    expect(accountsStore.accounts).toEqual(initialAccounts);
  });
});
