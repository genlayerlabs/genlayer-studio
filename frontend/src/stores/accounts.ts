import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import type { Address } from '@/types';
import { createAccount, generatePrivateKey } from 'genlayer-js';
import { useShortAddress } from '@/hooks';
import { notify } from '@kyvg/vue3-notification'
;import { useGenlayer } from '@/hooks';


export interface AccountInfo {
  type: 'local' | 'metamask';
  address: Address;
  privateKey?: Address; // Only for local accounts
}

export const useAccountsStore = defineStore('accountsStore', () => {
  const { shorten } = useShortAddress();

  // Store all accounts (both local and MetaMask)
  const accounts = ref<AccountInfo[]>([]);
  const selectedAccount = ref<AccountInfo | null>(null);

  // Migrate from old storage to new storage
  const storedKeys = localStorage.getItem('accountsStore.privateKeys');
  if (storedKeys) {
    const privateKeys = storedKeys.split(',') as Address[];
    accounts.value = privateKeys.map(createAccount);
    localStorage.removeItem('accountsStore.privateKeys');
    localStorage.removeItem('accountsStore.currentPrivateKey');
    localStorage.removeItem('accountsStore.accounts');
    _initAccountsLocalStorage();
  }

  // Initialize accounts from localStorage
  const storedAccounts = JSON.parse(
    localStorage.getItem('accountsStore.accounts') || '[]',
  );
  if (storedAccounts.length === 0) {
    generateNewAccount();
    _initAccountsLocalStorage();
  } else {
    accounts.value = storedAccounts;
  }

  // Initialize selected account from localStorage
  const storedSelectedAccount = JSON.parse(
    localStorage.getItem('accountsStore.currentAccount') || 'null',
  );
  setCurrentAccount(
    storedSelectedAccount ? storedSelectedAccount : accounts.value[0],
  );

  function _initAccountsLocalStorage() {
    localStorage.setItem(
      'accountsStore.accounts',
      JSON.stringify(accounts.value),
    );
    localStorage.setItem(
      'accountsStore.currentAccount',
      JSON.stringify(accounts.value[0]),
    );
  }

  async function fetchMetaMaskAccount() {
    if (!window.ethereum) {
      notify({
        title: 'MetaMask is not installed',
        type: 'error',
      });
      return;
    }

    const ethAccounts = await window.ethereum.request({
      method: 'eth_requestAccounts',
    });

    const metamaskAccount: AccountInfo = {
      type: 'metamask',
      address: ethAccounts[0] as Address,
    };

    // Update or add MetaMask account
    const existingMetaMaskIndex = accounts.value.findIndex(
      (acc) => acc.type === 'metamask',
    );
    if (existingMetaMaskIndex >= 0) {
      accounts.value[existingMetaMaskIndex] = metamaskAccount;
    } else {
      accounts.value.push(metamaskAccount);
    }

    setCurrentAccount(metamaskAccount);
  }

  if (window.ethereum) {
    window.ethereum.on('accountsChanged', (newAccounts: string[]) => {
      if (newAccounts.length > 0) {
        const metamaskAccount: AccountInfo = {
          type: 'metamask',
          address: newAccounts[0] as Address,
        };

        const existingMetaMaskIndex = accounts.value.findIndex(
          (acc) => acc.type === 'metamask',
        );
        if (existingMetaMaskIndex >= 0) {
          accounts.value[existingMetaMaskIndex] = metamaskAccount;
        }

        if (selectedAccount.value?.type === 'metamask') {
          setCurrentAccount(metamaskAccount);
        }
      } else {
        accounts.value = accounts.value.filter(
          (acc) => acc.type !== 'metamask',
        );
        setCurrentAccount(accounts.value[0]);
      }
      localStorage.setItem(
        'accountsStore.accounts',
        JSON.stringify(accounts.value),
      );
      localStorage.setItem(
        'accountsStore.currentAccount',
        JSON.stringify(selectedAccount.value),
      );
    });
  }

  function generateNewAccount(): AccountInfo {
    const privateKey = generatePrivateKey();
    const newAccountAddress = createAccount(privateKey).address;
    const newAccount: AccountInfo = {
      type: 'local',
      address: newAccountAddress,
      privateKey,
    };

    accounts.value.push(newAccount);
    setCurrentAccount(newAccount);
    return newAccount;
  }

  function removeAccount(accountToRemove: AccountInfo) {
    if (
      accounts.value.filter((acc) => acc.type === 'local').length <= 1 &&
      accountToRemove.type === 'local'
    ) {
      throw new Error('You need at least 1 local account');
    }

    accounts.value = accounts.value.filter(
      (acc) => acc.address !== accountToRemove.address,
    );

    if (selectedAccount.value?.address === accountToRemove.address) {
      const firstLocalAccount = accounts.value.find(
        (acc) => acc.type === 'local',
      );
      setCurrentAccount(firstLocalAccount || null);
    }
  }

  function setCurrentAccount(account: AccountInfo | null) {
    // Calling connect method here instead of relying on the UI button (shown only when
    // no MetaMask account was previously connected). If the user deletes the network,
    // that button won’t reappear; connecting on selection covers this edge case.
    if (account?.type === 'metamask') {
      const genlayer = useGenlayer();
      genlayer.client?.value?.connect(import.meta.env.VITE_NETWORK);
    }
    selectedAccount.value = account;
  }

  const displayAddress = computed(() => {
    if (!selectedAccount.value) return '0x';
    if (selectedAccount.value.address.startsWith('0x')) {
      if (selectedAccount.value.address.length !== 42) {
        return '0x';
      }
    } else if (selectedAccount.value.address.length !== 40) {
      return '0x';
    }

    try {
      return shorten(selectedAccount.value.address);
    } catch (err) {
      console.error(err);
      return '0x';
    }
  });

  const currentUserAddress = computed(() =>
    selectedAccount.value ? selectedAccount.value.address : '',
  );

  return {
    accounts,
    selectedAccount,
    currentUserAddress,
    fetchMetaMaskAccount,
    generateNewAccount,
    removeAccount,
    setCurrentAccount,
    displayAddress,
  };
});
