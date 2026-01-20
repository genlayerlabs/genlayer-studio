/**
 * Monaco Editor Autocomplete Provider for GenVM/GenLayer contracts
 *
 * Provides intelligent code completion for:
 * - gl.* module methods and properties
 * - Contract instances and their methods
 * - Address objects and their properties
 * - Decorators like @gl.public.view and @gl.public.write
 * - Common GenLayer patterns and constructs
 */

import * as Monaco from 'monaco-editor';
import {
  methodSignatures,
  moduleDescriptions,
  classDescriptions,
  propertyDescriptions,
} from './completionData';

/**
 * Main completion provider class for GenVM/GenLayer smart contracts
 */
export class GenVMCompletionProvider
  implements Monaco.languages.CompletionItemProvider
{
  private currentRange: Monaco.IRange | undefined;

  /**
   * Main entry point for providing completion items
   * Called by Monaco when user types or requests completions
   *
   * @param model - The text model
   * @param position - Current cursor position
   * @param context - Completion context (how it was triggered)
   * @param token - Cancellation token
   * @returns List of completion suggestions
   */
  public provideCompletionItems(
    model: Monaco.editor.ITextModel,
    position: Monaco.Position,
    context?: Monaco.languages.CompletionContext,
    token?: Monaco.CancellationToken,
  ): Monaco.languages.ProviderResult<Monaco.languages.CompletionList> {
    const line = model.getLineContent(position.lineNumber);
    const linePrefix = line.substring(0, position.column - 1);

    // Calculate the range for completions
    const word = model.getWordUntilPosition(position);
    this.currentRange = {
      startLineNumber: position.lineNumber,
      endLineNumber: position.lineNumber,
      startColumn: word.startColumn,
      endColumn: word.endColumn,
    } as Monaco.IRange;

    const items: Monaco.languages.CompletionItem[] = [];

    // Case 1: gl. completions - use regex to match patterns (with optional whitespace)
    if (linePrefix.match(/gl\.(\s*)$/)) {
      // Root gl. completions
      items.push(...this.createGlRootCompletions());
    } else if (linePrefix.match(/gl\.eq_principle\.(\s*)$/)) {
      // gl.eq_principle. completions
      items.push(...this.createEqPrincipleCompletions());
    } else if (linePrefix.match(/gl\.nondet\.(\s*)$/)) {
      // gl.nondet. completions
      items.push(...this.createNondetCompletions());
    } else if (linePrefix.match(/gl\.nondet\.web\.(\s*)$/)) {
      // gl.nondet.web. completions
      items.push(...this.createWebCompletions());
    } else if (linePrefix.match(/gl\.message\.(\s*)$/)) {
      // gl.message. completions
      items.push(...this.createMessageCompletions());
    } else if (linePrefix.match(/gl\.storage\.(\s*)$/)) {
      // gl.storage. completions
      items.push(...this.createStorageCompletions());
    } else if (linePrefix.match(/gl\.vm\.(\s*)$/)) {
      // gl.vm. completions
      items.push(...this.createVmCompletions());
    } else if (linePrefix.match(/gl\.advanced\.(\s*)$/)) {
      // gl.advanced. completions
      items.push(...this.createAdvancedCompletions());
    } else if (linePrefix.match(/gl\.evm\.(\s*)$/)) {
      // gl.evm. completions
      items.push(...this.createEvmCompletions());
    } else if (linePrefix.match(/gl\.public\.(\s*)$/)) {
      // gl.public. completions
      items.push(...this.createPublicCompletions());
    } else if (linePrefix.match(/gl\.public\.write\.(\s*)$/)) {
      // gl.public.write. completions
      items.push(...this.createPublicWriteCompletions());
    }

    // Case 2: Address constructor
    if (linePrefix.match(/\bAddress$/)) {
      const item: Monaco.languages.CompletionItem = {
        label: 'Address()',
        kind: Monaco.languages.CompletionItemKind.Constructor,
        insertText:
          'Address("${1:0x742d35Cc6634C0532925a3b8D4C9db96C4b4d8b6}")',
        insertTextRules:
          Monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
        detail: 'Address constructor',
        documentation: 'Create a new Address instance',
        range: this.currentRange!,
      };
      items.push(item);
    }

    // Case 3: Contract instance completions
    // Check if we're after .emit().
    if (linePrefix.match(/\.emit\(.*?\)\.(\s*)$/)) {
      items.push(...this.createEmitMethodCompletions());
    } else if (linePrefix.match(/\.view\(.*?\)\.(\s*)$/)) {
      // Check if we're after .view().
      items.push(...this.createViewMethodCompletions());
    } else if (
      linePrefix.match(/(\w+)\.(\s*)$/) &&
      this.mightBeContractInstance(model, position, linePrefix)
    ) {
      // Check if variable might be a contract instance
      items.push(...this.createContractInstanceCompletions());
    }

    // Case 4: Other variable completions (simple heuristic)
    const varMatch = linePrefix.match(/(\w+)\.(\s*)$/);
    if (varMatch && varMatch[1]) {
      const varName = varMatch[1];
      if (varName.includes('addr') || varName.includes('address')) {
        items.push(...this.createAddressMethodCompletions());
      } else if (varName.includes('response')) {
        items.push(...this.createResponseCompletions());
      }
    }

    return {
      suggestions: items,
      incomplete: false,
    };
  }

  /**
   * Create a basic completion item
   */
  private createCompletion(
    name: string,
    kind: Monaco.languages.CompletionItemKind,
    description: string,
  ): Monaco.languages.CompletionItem {
    return {
      label: name,
      kind: kind,
      detail: description,
      documentation: description,
      insertText: name,
      range: this.currentRange!,
    };
  }

  /**
   * Create a method completion with optional snippet
   */
  private createMethodCompletion(
    name: string,
    description: string,
  ): Monaco.languages.CompletionItem {
    const sigInfo = methodSignatures[name as keyof typeof methodSignatures];

    const item: Monaco.languages.CompletionItem = {
      label: sigInfo?.params ? `${name}${sigInfo.params}` : name,
      kind: Monaco.languages.CompletionItemKind.Method,
      detail: description,
      documentation: description,
      insertText: name,
      range: this.currentRange!,
    };

    // Use snippet for insert text if we have parameters
    if (sigInfo?.snippet) {
      item.insertText = name + sigInfo.snippet;
      item.insertTextRules =
        Monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet;
    } else {
      // No parameters, just add empty parentheses
      item.insertText = name + '()';
    }

    return item;
  }

  /**
   * Root gl. completions
   */
  private createGlRootCompletions(): Monaco.languages.CompletionItem[] {
    const items: Monaco.languages.CompletionItem[] = [];

    // Modules
    items.push(
      this.createCompletion(
        'eq_principle',
        Monaco.languages.CompletionItemKind.Module,
        moduleDescriptions.eq_principle,
      ),
    );
    items.push(
      this.createCompletion(
        'nondet',
        Monaco.languages.CompletionItemKind.Module,
        moduleDescriptions.nondet,
      ),
    );
    items.push(
      this.createCompletion(
        'message',
        Monaco.languages.CompletionItemKind.Module,
        moduleDescriptions.message,
      ),
    );
    items.push(
      this.createCompletion(
        'storage',
        Monaco.languages.CompletionItemKind.Module,
        moduleDescriptions.storage,
      ),
    );
    items.push(
      this.createCompletion(
        'vm',
        Monaco.languages.CompletionItemKind.Module,
        moduleDescriptions.vm,
      ),
    );
    items.push(
      this.createCompletion(
        'advanced',
        Monaco.languages.CompletionItemKind.Module,
        moduleDescriptions.advanced,
      ),
    );
    items.push(
      this.createCompletion(
        'evm',
        Monaco.languages.CompletionItemKind.Module,
        moduleDescriptions.evm,
      ),
    );
    items.push(
      this.createCompletion(
        'public',
        Monaco.languages.CompletionItemKind.Module,
        moduleDescriptions.public,
      ),
    );

    // Classes
    items.push(
      this.createCompletion(
        'Contract',
        Monaco.languages.CompletionItemKind.Class,
        classDescriptions.Contract,
      ),
    );
    items.push(
      this.createMethodCompletion('ContractAt', 'Contract proxy at address'),
    );
    items.push(
      this.createCompletion(
        'Event',
        Monaco.languages.CompletionItemKind.Class,
        classDescriptions.Event,
      ),
    );

    // Root methods
    items.push(this.createMethodCompletion('trace', 'Debug tracing output'));
    items.push(
      this.createMethodCompletion(
        'trace_time_micro',
        'Get runtime in microseconds',
      ),
    );
    items.push(
      this.createMethodCompletion(
        'deploy_contract',
        'Deploy a new GenVM contract',
      ),
    );
    items.push(
      this.createMethodCompletion(
        'get_contract_at',
        'Get contract proxy at address',
      ),
    );
    items.push(
      this.createMethodCompletion(
        'contract_interface',
        'Contract interface decorator',
      ),
    );

    return items;
  }

  /**
   * gl.eq_principle. completions
   */
  private createEqPrincipleCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createMethodCompletion(
        'strict_eq',
        methodSignatures.strict_eq.description,
      ),
      this.createMethodCompletion(
        'prompt_comparative',
        methodSignatures.prompt_comparative.description,
      ),
      this.createMethodCompletion(
        'prompt_non_comparative',
        methodSignatures.prompt_non_comparative.description,
      ),
    ];
  }

  /**
   * gl.nondet. completions
   */
  private createNondetCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createCompletion(
        'web',
        Monaco.languages.CompletionItemKind.Module,
        moduleDescriptions.web,
      ),
      this.createMethodCompletion(
        'exec_prompt',
        methodSignatures.exec_prompt.description,
      ),
    ];
  }

  /**
   * gl.nondet.web. completions
   */
  private createWebCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createMethodCompletion(
        'render',
        methodSignatures.render.description,
      ),
      this.createMethodCompletion(
        'request',
        methodSignatures.request.description,
      ),
      this.createMethodCompletion('get', methodSignatures.get.description),
      this.createMethodCompletion('post', methodSignatures.post.description),
      this.createMethodCompletion(
        'delete',
        methodSignatures.delete.description,
      ),
      this.createMethodCompletion('head', methodSignatures.head.description),
      this.createMethodCompletion('patch', methodSignatures.patch.description),
    ];
  }

  /**
   * gl.message. completions
   */
  private createMessageCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createCompletion(
        'sender',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.sender,
      ),
      this.createCompletion(
        'sender_address',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.sender_address,
      ),
      this.createCompletion(
        'contract_address',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.contract_address,
      ),
      this.createCompletion(
        'value',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.value,
      ),
      this.createCompletion(
        'chain_id',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.chain_id,
      ),
      this.createCompletion(
        'data',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.data,
      ),
    ];
  }

  /**
   * gl.storage. completions
   */
  private createStorageCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createMethodCompletion(
        'inmem_allocate',
        methodSignatures.inmem_allocate.description,
      ),
      this.createMethodCompletion(
        'copy_to_memory',
        methodSignatures.copy_to_memory.description,
      ),
      this.createCompletion(
        'Root',
        Monaco.languages.CompletionItemKind.Class,
        classDescriptions.Root,
      ),
    ];
  }

  /**
   * Address method completions
   */
  private createAddressMethodCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createCompletion(
        'as_hex',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.as_hex,
      ),
      this.createCompletion(
        'as_bytes',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.as_bytes,
      ),
      this.createCompletion(
        'as_b64',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.as_b64,
      ),
      this.createCompletion(
        'as_int',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.as_int,
      ),
    ];
  }

  /**
   * Response completions
   */
  private createResponseCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createCompletion(
        'status',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.status,
      ),
      this.createCompletion(
        'headers',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.headers,
      ),
      this.createCompletion(
        'body',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.body,
      ),
    ];
  }

  /**
   * gl.vm. completions
   */
  private createVmCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createCompletion(
        'UserError',
        Monaco.languages.CompletionItemKind.Class,
        classDescriptions.UserError,
      ),
      this.createCompletion(
        'VMError',
        Monaco.languages.CompletionItemKind.Class,
        classDescriptions.VMError,
      ),
      this.createCompletion(
        'Return',
        Monaco.languages.CompletionItemKind.Class,
        classDescriptions.Return,
      ),
      this.createCompletion(
        'Result',
        Monaco.languages.CompletionItemKind.Class,
        classDescriptions.Result,
      ),
      this.createMethodCompletion(
        'spawn_sandbox',
        methodSignatures.spawn_sandbox.description,
      ),
      this.createMethodCompletion(
        'run_nondet',
        methodSignatures.run_nondet.description,
      ),
      this.createMethodCompletion(
        'run_nondet_unsafe',
        methodSignatures.run_nondet_unsafe.description,
      ),
      this.createMethodCompletion(
        'unpack_result',
        methodSignatures.unpack_result.description,
      ),
    ];
  }

  /**
   * gl.advanced. completions
   */
  private createAdvancedCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createMethodCompletion(
        'user_error_immediate',
        methodSignatures.user_error_immediate.description,
      ),
    ];
  }

  /**
   * gl.evm. completions
   */
  private createEvmCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createMethodCompletion(
        'contract_interface',
        methodSignatures.contract_interface.description,
      ),
      this.createCompletion(
        'MethodEncoder',
        Monaco.languages.CompletionItemKind.Class,
        classDescriptions.MethodEncoder,
      ),
      this.createMethodCompletion(
        'encode',
        methodSignatures.encode.description,
      ),
      this.createMethodCompletion(
        'decode',
        methodSignatures.decode.description,
      ),
      this.createMethodCompletion(
        'selector_of',
        methodSignatures.selector_of.description,
      ),
      this.createMethodCompletion(
        'signature_of',
        methodSignatures.signature_of.description,
      ),
      this.createMethodCompletion(
        'type_name_of',
        methodSignatures.type_name_of.description,
      ),
      this.createCompletion(
        'ContractProxy',
        Monaco.languages.CompletionItemKind.Class,
        classDescriptions.ContractProxy,
      ),
      this.createCompletion(
        'ContractDeclaration',
        Monaco.languages.CompletionItemKind.Class,
        classDescriptions.ContractDeclaration,
      ),
      this.createCompletion(
        'bytes32',
        Monaco.languages.CompletionItemKind.Class,
        classDescriptions.bytes32,
      ),
    ];
  }

  /**
   * gl.public. completions
   */
  private createPublicCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createCompletion(
        'view',
        Monaco.languages.CompletionItemKind.Method,
        'Public view method decorator',
      ),
      this.createCompletion(
        'write',
        Monaco.languages.CompletionItemKind.Module,
        'Public write method decorator',
      ),
    ];
  }

  /**
   * gl.public.write. completions
   */
  private createPublicWriteCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createCompletion(
        'payable',
        Monaco.languages.CompletionItemKind.Method,
        methodSignatures.payable.description,
      ),
      this.createMethodCompletion(
        'min_gas',
        methodSignatures.min_gas.description,
      ),
    ];
  }

  /**
   * Contract instance completions
   */
  private createContractInstanceCompletions(): Monaco.languages.CompletionItem[] {
    const items: Monaco.languages.CompletionItem[] = [];

    // Methods that return something for chaining
    items.push(
      this.createMethodCompletion('emit', methodSignatures.emit.description),
    );
    items.push(
      this.createMethodCompletion('view', methodSignatures.view.description),
    );
    items.push(
      this.createMethodCompletion(
        'emit_transfer',
        methodSignatures.emit_transfer.description,
      ),
    );

    // Properties (no parentheses)
    items.push(
      this.createCompletion(
        'balance',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.balance,
      ),
    );
    items.push(
      this.createCompletion(
        'address',
        Monaco.languages.CompletionItemKind.Property,
        propertyDescriptions.address,
      ),
    );

    return items;
  }

  /**
   * Emit method completions for chained calls
   */
  private createEmitMethodCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createMethodCompletion(
        'send_message',
        methodSignatures.send_message.description,
      ),
      this.createMethodCompletion(
        'transfer',
        methodSignatures.transfer.description,
      ),
      this.createMethodCompletion('mint', methodSignatures.mint.description),
      this.createMethodCompletion(
        'update_storage',
        methodSignatures.update_storage.description,
      ),
    ];
  }

  /**
   * View method completions for chained calls
   */
  private createViewMethodCompletions(): Monaco.languages.CompletionItem[] {
    return [
      this.createMethodCompletion(
        'get_balance_of',
        methodSignatures.get_balance_of.description,
      ),
      this.createMethodCompletion(
        'balance_of',
        methodSignatures.balance_of.description,
      ),
      this.createMethodCompletion(
        'total_supply',
        methodSignatures.total_supply.description,
      ),
      this.createMethodCompletion('get_name', 'Get contract name'),
      this.createMethodCompletion('get_symbol', 'Get token symbol'),
      this.createMethodCompletion('owner', 'Get contract owner'),
    ];
  }

  /**
   * Check if a variable might be a contract instance
   * Uses heuristics to determine if a variable is likely a contract proxy
   */
  private mightBeContractInstance(
    model: Monaco.editor.ITextModel,
    position: Monaco.Position,
    linePrefix: string,
  ): boolean {
    const varMatch = linePrefix.match(/(\w+)\.(\s*)$/);
    if (!varMatch || !varMatch[1]) return false;

    const varName = varMatch[1];

    // Look backwards through the document for assignment
    const startLine = Math.max(1, position.lineNumber - 50);
    for (let i = position.lineNumber; i >= startLine; i--) {
      const line = model.getLineContent(i);
      // Check if this variable was assigned from gl.ContractAt
      const assignPattern = new RegExp(`${varName}\\s*=\\s*gl\\.ContractAt\\(`);
      if (assignPattern.test(line)) {
        return true;
      }
      // Also check for gl.get_contract_at
      const getContractPattern = new RegExp(
        `${varName}\\s*=\\s*gl\\.get_contract_at\\(`,
      );
      if (getContractPattern.test(line)) {
        return true;
      }
    }

    // Check if variable name suggests it's a contract
    return (
      varName.includes('contract') ||
      varName.includes('token') ||
      varName.includes('bridge')
    );
  }
}

/**
 * Initialize and register the GenVM autocomplete provider
 * Should be called once when setting up the Monaco editor
 *
 * @param monaco - Monaco editor namespace
 * @returns Disposable to clean up the provider
 */
export function setupGenVMAutocomplete(
  monaco: typeof Monaco,
): Monaco.IDisposable {
  // Create provider instance
  const provider = new GenVMCompletionProvider();

  // Register with trigger characters to auto-show on dot
  const disposable = monaco.languages.registerCompletionItemProvider('python', {
    provideCompletionItems: (
      model: Monaco.editor.ITextModel,
      position: Monaco.Position,
      context: Monaco.languages.CompletionContext,
      token: Monaco.CancellationToken,
    ) => provider.provideCompletionItems(model, position, context, token),
    triggerCharacters: ['.'],
  });

  return disposable;
}
