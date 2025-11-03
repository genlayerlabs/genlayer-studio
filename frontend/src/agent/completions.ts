// Lightweight AI-like completions and hovers based on curated docs/patterns

type Monaco = any;

const DOC_FIRST_CONTRACT = `Contract shape:\n- Header version + Depends\n- from genlayer import *\n- One class extends gl.Contract\n- __init__ not public\n- Public methods decorated with @gl.public.view or @gl.public.write`;

const DOC_ADDRESS = `Address type (20 bytes). Create from hex/b64/bytes. Props: as_hex, as_bytes, as_b64, as_int; string/format: str(addr), f"{addr:x}", f"{addr:b64}", f"{addr:cd}".`;
const DOC_COLLECTIONS = `Storage-compatible collections: DynArray[T] (instead of list[T]); TreeMap[K,V] (instead of dict[K,V]). Only fully instantiated generics allowed. Calldata maps support str keys only.`;
const DOC_DATA_CLASSES = `Use @allow_storage + @dataclass for storage structs. For generic storage classes, allocate with gl.storage.inmem_allocate(Type[...], *args).`;
const DOC_EQ = `Equivalence Principle (nondeterministic blocks) via gl.eq_principle.strict_eq(...) or gl.vm.run_nondet(...). Use only within allowed nondet sections.`;

function snippet(label: string, insertText: string, detail: string, documentation?: string) {
  return {
    label,
    kind: 15, // Snippet
    insertText,
    insertTextRules: 4, // InsertAsSnippet
    detail,
    documentation,
    sortText: label.startsWith('Insert: contract header') || label.startsWith('contract:header') ? '000' : '999',
  };
}

export function registerAIAgentProviders(monaco: Monaco) {
  if (!monaco?.languages) return { dispose() {} };
  // Prevent double registration if component remounts
  const key = '__glAgentRegistered';
  if ((monaco as any)[key]) return { dispose() {} };
  (monaco as any)[key] = true;

  const disposables: any[] = [];

  // Completion provider for Python
  // Example header snippets (typed by typing '#')
  const EXAMPLE_HEADERS = [
    {
      label: 'Insert: contract header (1-2)',
      text: `# v0.1.0\n# { "Depends": "py-genlayer:latest" }\n`,
      detail: 'Insert two standard contract header lines',
    },
  ];

  disposables.push(
    monaco.languages.registerCompletionItemProvider('python', {
      triggerCharacters: ['#', 'f', 'i', 'c', 'd', '@', 'p', 'r', '.'],
      provideCompletionItems: (model: any, position: any) => {
        const suggestions: any[] = [];
        const line = model.getLineContent(position.lineNumber).slice(0, position.column - 1);
        const trimmed = line.trim();
        const isTop = position.lineNumber <= 3 && model.getValue().trim().length < 200;
        const uniq = new Set<string>();
        const add = (s: any) => { if (!uniq.has(s.label)) { uniq.add(s.label); suggestions.push(s); } };

        if (isTop) {
          add(snippet(
              'contract:header+import',
              `# v0.1.0\n# { "Depends": "py-genlayer:latest" }\n\nfrom genlayer import *\n`,
              'Insert GenLayer version header and import',
              DOC_FIRST_CONTRACT,
            ));
          // Also provide independent header/import pieces
          add(snippet(
            'contract:header only',
            `# v0.1.0\n# { "Depends": "py-genlayer:latest" }\n`,
            'Insert only the version + Depends header',
            DOC_FIRST_CONTRACT,
          ));
          add(snippet(
            'contract:import stdlib',
            `from genlayer import *\n`,
            'Import GenLayer stdlib',
            DOC_FIRST_CONTRACT,
          ));
        }

        // If typing a comment at the top, suggest example headers
        if (position.lineNumber === 1 && trimmed.startsWith('#')) {
          EXAMPLE_HEADERS.forEach((h) => add(snippet(h.label, h.text, h.detail, 'Header snippet')));
        }

        add(snippet(
            'contract:class',
            `class ${'${1:MyContract}'}(gl.Contract):\n    ${'${2:value}'}: u256\n\n    def __init__(self, ${'${3:initial}'}: u256):\n        self.${'${2:value}'} = ${'${3:initial}'}\n`,
            'Create contract class skeleton',
            DOC_FIRST_CONTRACT,
          ));

        // "Your First Contract" example (Hello contract)
        add(snippet(
          'first-contract: hello example',
          `# v0.1.0\n# { "Depends": "py-genlayer:latest" }\n\nfrom genlayer import *\n\nclass Hello(gl.Contract):\n    name: str\n\n    def __init__(self, name: str):\n        self.name = name\n\n    @gl.public.view\n    def run(self) -> str:\n        return f'Hello, {self.name}'\n\n    @gl.public.write\n    def set_name(self, name: str):\n        # debug prints are allowed; included in execution log\n        print(f'debug old name: {self.name}')\n        self.name = name\n`,
          'Hello contract from First Contract docs',
          DOC_FIRST_CONTRACT,
        ));

        // Decorator payable variant from docs
        add(snippet(
          'decorator:@gl.public.write.payable',
          '@gl.public.write.payable\n',
          'Add @gl.public.write.payable decorator',
        ));

        // Storage and typing rules from docs
        add(snippet(
          'storage: declare typed fields',
          `class ${'${1:Contract}'}(gl.Contract):\n    name: str\n    balance: u256\n    tags: DynArray[str]\n    accounts: TreeMap[Address, u256]\n`,
          'Declare persistent fields (must be typed and inside class body)'
        ));
        add(snippet(
          'types: bigint note',
          `# Prefer fixed-size integers like u256; use bigint only if truly needed\namount: bigint\n`,
          'Use fixed-size ints; bigint is an alias of Python int'
        ));
        add(snippet(
          'collections: list->DynArray reminder',
          `# list[T] is not storage-compatible; use DynArray[T]\nitems: DynArray[${'${1:T}'}]\n`,
          'DynArray replaces list for storage collections',
          DOC_COLLECTIONS,
        ));
        add(snippet(
          'collections: dict->TreeMap reminder',
          `# dict[K,V] is not storage-compatible; use TreeMap[K,V] (keys fully typed)\nmap: TreeMap[${'${1:K}'}, ${'${2:V}'}]\n`,
          'TreeMap replaces dict for storage mappings',
          DOC_COLLECTIONS,
        ));
        add(snippet(
          'contract: single class comment',
          `# Note: one contract class per file; constructor (__init__) must not be decorated\n`,
          'Doc hint about single contract per file'
        ));

        add(snippet(
            'method:view:get',
            `@gl.public.view\ndef ${'${1:get_value}'}(self) -> u256:\n    return self.${'${2:value}'}\n`,
            'Public read-only method',
          ));
        add(snippet(
          'method:view:with params',
          `@gl.public.view\ndef ${'${1:format_name}'}(self, first: str, last: str) -> str:\n    return f"{first} {last}"\n`,
          'View method with parameters and string return',
        ));

        add(snippet(
            'method:write:set',
            `@gl.public.write\ndef ${'${1:set_value}'}(self, v: u256) -> None:\n    self.${'${2:value}'} = v\n`,
            'Public state-mutating method',
          ));
        add(snippet(
          'method:write:payable',
          `@gl.public.write.payable\ndef ${'${1:deposit}'}(self, amount: u256) -> None:\n    # TODO: update state with amount\n    pass\n`,
          'Payable write method template',
        ));
        add(snippet(
          'method:debug print',
          `@gl.public.write\ndef ${'${1:set_name}'}(self, name: str) -> None:\n    print(f"debug old name: {self.name}")\n    self.name = name\n`,
          'Write method with debug print (execution log)',
        ));

        // Address helpers
        add(snippet(
          'Address: construct & to hex',
          `addr = Address(${"'${1:0x...}'"})\nhex_str = addr.as_hex\n`,
          'Construct Address and get hex',
          DOC_ADDRESS,
        ));
        add(snippet(
          'Address: conversions',
          `addr = Address(${"'${1:0x...}'"})\nbytes_ = addr.as_bytes\nb64 = addr.as_b64\nnum = addr.as_int\n`,
          'Address conversions to bytes/base64/int',
          DOC_ADDRESS,
        ));
        add(snippet(
          'Address: format helpers',
          `addr = Address(${"'${1:0x...}'"})\nhex_fmt = f"{addr:x}"\nb64_fmt = f"{addr:b64}"\ncd_fmt = f"{addr:cd}"\n`,
          'Format Address using x/b64/cd specifiers',
          DOC_ADDRESS,
        ));

        // Collections (DynArray / TreeMap)
        add(snippet(
          'Storage: DynArray[T]',
          `from genlayer import *\nclass ${'${1:ArrayOps}'}(gl.Contract):\n    items: DynArray[u256]\n\n    def __init__(self):\n        self.items = DynArray[u256]()\n\n    @gl.public.write\n    def add(self, n: u256):\n        self.items.append(n)\n\n    @gl.public.view\n    def length(self) -> u256:\n        return u256(len(self.items))\n`,
          'DynArray storage pattern with append/length',
          DOC_COLLECTIONS,
        ));
        add(snippet(
            'Storage: TreeMap[K,V]',
          `from genlayer import *\nclass ${'${1:MapOps}'}(gl.Contract):\n    balances: TreeMap[Address, u256]\n\n    def __init__(self):\n        self.balances = TreeMap[Address, u256]()\n\n    @gl.public.write\n    def set_balance(self, account_hex: str, amount: u256):\n        account = Address(account_hex)\n        self.balances[account] = amount\n\n    @gl.public.view\n    def get_balance(self, account_hex: str) -> u256:\n        account = Address(account_hex)\n        return self.balances.get(account, u256(0))\n`,
          'TreeMap storage pattern with set/get',
            DOC_COLLECTIONS,
        ));

        // Dataclass for storage
        add(snippet(
          'Dataclass: @allow_storage',
          `from dataclasses import dataclass\n@allow_storage\n@dataclass\nclass ${'${1:User}'}:\n    name: str\n    balance: u256\n`,
          'Storage dataclass pattern',
          DOC_DATA_CLASSES,
        ));
        add(snippet(
          'Dataclass: generic + inmem_allocate',
          `from dataclasses import dataclass\n@allow_storage\n@dataclass\nclass ${'${1:Gen}'}[T]:\n    data: DynArray[T]\n\n# Allocate generic storage in memory\nval = gl.storage.inmem_allocate(${"${1:Gen}"}[bytes])\n`,
          'Generic storage allocation example',
          DOC_DATA_CLASSES,
        ));

        // Equivalence Principle skeleton (commented for safety)
        add(snippet(
            'EP: strict_eq block (skeleton)',
            `# def nd():\n#     # perform nondeterministic operation here\n#     return True\n# self.result = gl.eq_principle.strict_eq(nd)\n`,
            'Nondeterministic block skeleton (use carefully)',
            DOC_EQ,
          ));
        add(snippet(
          'EP: run_nondet (leader/validator) skeleton',
          `# def leader_fn():\n#     # produce result\n#     return 1\n#\n# def validator_fn(leader_result):\n#     # validate leader_result\n#     return isinstance(leader_result, int)\n#\n# self.out = gl.vm.run_nondet(leader_fn, validator_fn)\n`,
          'Leader/validator pattern skeleton',
          DOC_EQ,
        ));

        // Context-sensitive completions based on current prefix
        if (/^from\s*$/i.test(trimmed) || /^from\s+g?$/i.test(trimmed)) {
          add(snippet('from genlayer import *', 'from genlayer import *\n', 'Import GenLayer stdlib'));
        }
        if (/^impor?$/i.test(trimmed) || /^import\s*$/i.test(trimmed)) {
          add(snippet('import dataclasses', 'from dataclasses import dataclass\n', 'Import dataclass'));
          add(snippet('import Address', 'from genlayer import *\n# Address, DynArray, TreeMap available via stdlib\n', 'Import GenLayer stdlib'));
        }
        if (/^Address\b/.test(trimmed)) {
          add(snippet('Address: construct & to hex', `addr = Address(${"'${1:0x...}'"})\nhex_str = addr.as_hex\n`, 'Construct Address and get hex', DOC_ADDRESS));
          add(snippet('Address: conversions', `addr = Address(${"'${1:0x...}'"})\nbytes_ = addr.as_bytes\nb64 = addr.as_b64\nnum = addr.as_int\n`, 'Address conversions to bytes/base64/int', DOC_ADDRESS));
        }
        if (/^DynArray\b/.test(trimmed) || /^TreeMap\b/.test(trimmed)) {
          add(snippet('Storage: DynArray[T]', `from genlayer import *\nclass ${'${1:ArrayOps}'}(gl.Contract):\n    items: DynArray[u256]\n\n    def __init__(self):\n        self.items = DynArray[u256]()\n`, 'DynArray storage pattern', DOC_COLLECTIONS));
          add(snippet('Storage: TreeMap[K,V]', `from genlayer import *\nclass ${'${1:MapOps}'}(gl.Contract):\n    balances: TreeMap[Address, u256]\n\n    def __init__(self):\n        self.balances = TreeMap[Address, u256]()\n`, 'TreeMap storage pattern', DOC_COLLECTIONS));
        }
        if (/^class\s*$/i.test(trimmed)) {
          add(snippet('contract:class', `class ${'${1:MyContract}'}(gl.Contract):\n    ${'${2:value}'}: u256\n\n    def __init__(self, ${'${3:initial}'}: u256):\n        self.${'${2:value}'} = ${'${3:initial}'}\n`, 'Create contract class skeleton', DOC_FIRST_CONTRACT));
        }
        if (/^def\s*$/i.test(trimmed)) {
          add(snippet('method:view:get', `@gl.public.view\ndef ${'${1:get_value}'}(self) -> u256:\n    return self.${'${2:value}'}\n`, 'Public read-only method'));
          add(snippet('method:write:set', `@gl.public.write\ndef ${'${1:set_value}'}(self, v: u256) -> None:\n    self.${'${2:value}'} = v\n`, 'Public state-mutating method'));
        }
        // Suggest decorators when typing '@' or '@gl.'
        if (/^@$/i.test(trimmed) || trimmed.endsWith('@') || /@gl\.?$/i.test(trimmed) || /@gl\.p?$/i.test(trimmed)) {
          add(snippet('decorator: @gl.public.view', '@gl.public.view\n', 'Add view decorator'));
          add(snippet('decorator: @gl.public.write', '@gl.public.write\n', 'Add write decorator'));
          add(snippet('decorator: @gl.public.write.payable', '@gl.public.write.payable\n', 'Add payable write decorator'));
        }

        // Contextual decorator assists
        if (/^\s*def\s+/.test(line)) {
          add(snippet('decorator:@gl.public.view', '@gl.public.view\n', 'Add @gl.public.view'));
          add(snippet('decorator:@gl.public.write', '@gl.public.write\n', 'Add @gl.public.write'));
        }

        return { suggestions };
      },
    }),
  );

  // Hover provider for docs on keywords
  disposables.push(
    monaco.languages.registerHoverProvider('python', {
      provideHover: (model: any, position: any) => {
        const word = model.getWordAtPosition(position)?.word || '';
        if (word === 'Address') {
          return { contents: [{ value: `**Address**\n\n${DOC_ADDRESS}` }] };
        }
        if (word === 'DynArray' || word === 'TreeMap') {
          return { contents: [{ value: `**Collections**\n\n${DOC_COLLECTIONS}` }] };
        }
        if (word === 'allow_storage') {
          return { contents: [{ value: `**@allow_storage**\n\n${DOC_DATA_CLASSES}` }] };
        }
        if (word === 'gl' || word === 'Contract') {
          return { contents: [{ value: `**GenLayer Contract**\n\n${DOC_FIRST_CONTRACT}` }] };
        }
        return null as any;
      },
    }),
  );

  return {
    dispose() {
      disposables.forEach((d) => d?.dispose?.());
      try {
        delete (monaco as any)[key];
      } catch (_) {
        (monaco as any)[key] = false;
      }
    },
  };
}


