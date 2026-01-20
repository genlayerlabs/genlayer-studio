// global.d.ts
interface Window {
  ethereum?: {
    isMetaMask?: boolean;
    request: (args: { method: string; params?: unknown[] }) => Promise<Array>;
    on: (method: string, callback: Function) => {};
  };
}

// Monaco Editor ESM module type declarations
declare module 'monaco-editor/esm/vs/editor/editor.api' {
  export * from 'monaco-editor';
}
