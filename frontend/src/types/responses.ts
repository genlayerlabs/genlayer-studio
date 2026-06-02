export interface JsonRPCResponse<T> {
  id: string;
  jsonrpc: string;
  result: T;
  error?: {
    code: number;
    message: string;
    data?: any;
  };
}
