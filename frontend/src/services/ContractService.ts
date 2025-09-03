import { useGenlayer } from '@/hooks';
import type { Address } from 'genlayer-js/types';

export class ContractService {
  /**
   * Verifies if a contract exists at the given address
   * @param address The contract address to verify
   * @returns Promise<boolean> indicating if contract exists
   */
  static async verifyContractExists(address: string): Promise<boolean> {
    try {
      const genlayer = useGenlayer();
      const client = genlayer.client.value;

      if (!client) {
        throw new Error('Genlayer client not initialized');
      }

      // Try to get contract schema to verify it exists
      const schema = await client.getContractSchema(address as Address);
      return !!schema;
    } catch (error) {
      console.error('Error verifying contract:', error);
      return false;
    }
  }
}
