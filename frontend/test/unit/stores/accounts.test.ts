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

vi.mock('@/hooks', () => ({
  useGenlayer: vi.fn(),
  useShortAddress: vi.fn(() => ({})),
}));

vi.mock('genlayer-js', () => ({
  createAccount: vi.fn(() => ({ address: testAddress1 })),
  generatePrivateKey: vi.fn(() => testKey1),
}));

describe('useAccountsStore', () => {
  let accountsStore: ReturnType<typeof useAccountsStore>;
  const mockGenlayerClient = {
    getTransaction: vi.fn(),
  };

  beforeEach(() => {
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

    accountsStore = useAccountsStore();

    mockGenlayerClient.getTransaction.mockClear();
    (localStorage.getItem as Mock).mockClear();
    (localStorage.getItem as Mock).mockClear();
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
      address: testAccount,
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
