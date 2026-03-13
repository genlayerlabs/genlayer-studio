'use client';

import { useState, useEffect, useCallback } from 'react';
import { CodeBlock } from '@/components/CodeBlock';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import { ChevronRight, Play, Loader2, AlertCircle, Eye, Pencil, FileCode, Info } from 'lucide-react';
import {
  fetchContractSchema,
  callReadMethod,
  parseParamValue,
  type ContractSchema,
  type ContractMethod,
} from '@/lib/contractSchema';

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface ContractInteractionProps {
  address: string;
  code: string | null;
}

export function ContractInteraction({ address, code }: ContractInteractionProps) {
  const [schema, setSchema] = useState<ContractSchema | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [schemaError, setSchemaError] = useState<string | null>(null);

  useEffect(() => {
    setSchemaLoading(true);
    setSchemaError(null);
    fetchContractSchema(address)
      .then(setSchema)
      .catch((e) => setSchemaError(e.message))
      .finally(() => setSchemaLoading(false));
  }, [address]);

  const readMethods = schema
    ? Object.entries(schema.methods).filter(([, m]) => m.readonly)
    : [];
  const writeMethods = schema
    ? Object.entries(schema.methods).filter(([, m]) => !m.readonly)
    : [];

  return (
    <Tabs defaultValue="code">
      <TabsList>
        <TabsTrigger value="code" className="flex items-center gap-1.5">
          <FileCode className="w-4 h-4" />
          Code
        </TabsTrigger>
        <TabsTrigger value="read" className="flex items-center gap-1.5">
          <Eye className="w-4 h-4" />
          Read Contract
          {readMethods.length > 0 && (
            <Badge variant="secondary" className="ml-1 text-xs">{readMethods.length}</Badge>
          )}
        </TabsTrigger>
        <TabsTrigger value="write" className="flex items-center gap-1.5">
          <Pencil className="w-4 h-4" />
          Write Contract
          {writeMethods.length > 0 && (
            <Badge variant="secondary" className="ml-1 text-xs">{writeMethods.length}</Badge>
          )}
        </TabsTrigger>
      </TabsList>

      <TabsContent value="code">
        <Card>
          <CardContent className="p-6">
            {code ? <CodeBlock code={code} /> : <p className="text-muted-foreground">No source code available</p>}
          </CardContent>
        </Card>
      </TabsContent>

      <TabsContent value="read">
        <Card>
          <CardContent className="p-6">
            {schemaLoading && (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin" />
                Loading contract schema...
              </div>
            )}
            {schemaError && (
              <div className="flex items-center gap-2 text-destructive">
                <AlertCircle className="w-4 h-4" />
                {schemaError}
              </div>
            )}
            {schema && readMethods.length === 0 && (
              <p className="text-muted-foreground">No read methods found</p>
            )}
            {readMethods.length > 0 && (
              <div className="space-y-2">
                {readMethods.map(([name, method], i) => (
                  <MethodForm
                    key={name}
                    index={i + 1}
                    name={name}
                    method={method}
                    address={address}
                    executable
                  />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </TabsContent>

      <TabsContent value="write">
        <Card>
          <CardContent className="p-6">
            <div className="flex items-start gap-3 p-4 mb-4 rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/50">
              <Info className="w-5 h-5 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-blue-900 dark:text-blue-200">Write methods require a connected wallet</p>
                <p className="text-sm text-blue-700 dark:text-blue-400 mt-1">
                  To execute write transactions, use <strong>GenLayer Studio</strong> where you can sign and submit transactions.
                </p>
              </div>
            </div>
            {schemaLoading && (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin" />
                Loading contract schema...
              </div>
            )}
            {schemaError && (
              <div className="flex items-center gap-2 text-destructive">
                <AlertCircle className="w-4 h-4" />
                {schemaError}
              </div>
            )}
            {schema && writeMethods.length === 0 && (
              <p className="text-muted-foreground">No write methods found</p>
            )}
            {writeMethods.length > 0 && (
              <div className="space-y-2">
                {writeMethods.map(([name, method], i) => (
                  <MethodForm
                    key={name}
                    index={i + 1}
                    name={name}
                    method={method}
                    address={address}
                    executable={false}
                  />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </TabsContent>
    </Tabs>
  );
}

// ---------------------------------------------------------------------------
// MethodForm — a single collapsible method with inputs and (optional) execute
// ---------------------------------------------------------------------------

function MethodForm({
  index,
  name,
  method,
  address,
  executable,
}: {
  index: number;
  name: string;
  method: ContractMethod;
  address: string;
  executable: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [inputs, setInputs] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const [pName] of method.params) init[pName] = '';
    return init;
  });
  const [result, setResult] = useState<unknown>(undefined);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExecute = useCallback(async () => {
    setLoading(true);
    setError(null);
    setResult(undefined);
    try {
      const args = method.params.map(([pName, pType]) =>
        parseParamValue(inputs[pName] || '', pType)
      );
      const res = await callReadMethod(address, name, args);
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [address, name, method.params, inputs]);

  const hasParams = method.params.length > 0;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center gap-2 w-full text-left px-3 py-2.5 rounded-lg hover:bg-muted transition-colors group">
        <ChevronRight className={`w-4 h-4 text-muted-foreground transition-transform ${open ? 'rotate-90' : ''}`} />
        <span className="text-sm text-muted-foreground">{index}.</span>
        <span className="text-sm font-medium text-foreground">{name}</span>
        {!hasParams && executable && (
          <Badge variant="outline" className="text-xs ml-auto">no args</Badge>
        )}
        {method.payable && (
          <Badge className="bg-yellow-50 dark:bg-yellow-950 text-yellow-700 dark:text-yellow-400 border-yellow-200 dark:border-yellow-800 text-xs">
            payable
          </Badge>
        )}
      </CollapsibleTrigger>
      <CollapsibleContent className="pl-9 pr-3 pb-3">
        <div className="space-y-3 mt-1">
          {/* Parameter inputs */}
          {method.params.map(([pName, pType]) => (
            <div key={pName}>
              <label className="text-xs text-muted-foreground block mb-1">
                {pName} <span className="text-muted-foreground/60">({pType})</span>
              </label>
              {executable ? (
                <Input
                  value={inputs[pName] || ''}
                  onChange={(e) => setInputs((prev) => ({ ...prev, [pName]: e.target.value }))}
                  placeholder={pType === 'bool' ? 'true / false' : pType}
                  className="font-mono text-sm h-8"
                />
              ) : (
                <div className="bg-muted rounded-md px-3 py-1.5 text-sm font-mono text-muted-foreground">
                  {pType}
                </div>
              )}
            </div>
          ))}

          {/* Return type */}
          {method.ret && (
            <div className="text-xs text-muted-foreground">
              Returns: <code className="bg-muted px-1.5 py-0.5 rounded">{method.ret}</code>
            </div>
          )}

          {/* Execute button (read only) */}
          {executable && (
            <Button
              size="sm"
              onClick={handleExecute}
              disabled={loading}
              className="gap-1.5"
            >
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
              Query
            </Button>
          )}

          {/* Result */}
          {result !== undefined && (
            <div className="bg-muted rounded-lg p-3">
              <div className="text-xs text-muted-foreground mb-1">Result</div>
              <code className="text-sm font-mono text-foreground break-all">
                {formatResult(result)}
              </code>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="bg-red-50 dark:bg-red-950/50 border border-red-200 dark:border-red-800 rounded-lg p-3">
              <div className="text-xs text-red-600 dark:text-red-400 mb-1">Error</div>
              <code className="text-sm text-red-800 dark:text-red-300 break-all">{error}</code>
            </div>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatResult(val: unknown): string {
  if (val === null || val === undefined) return 'null';
  if (typeof val === 'bigint') return val.toString();
  if (val instanceof Map) {
    const obj: Record<string, unknown> = {};
    val.forEach((v, k) => { obj[String(k)] = v; });
    return JSON.stringify(obj, replacer, 2);
  }
  if (typeof val === 'object') {
    return JSON.stringify(val, replacer, 2);
  }
  return String(val);
}

function replacer(_key: string, value: unknown): unknown {
  if (typeof value === 'bigint') return value.toString();
  return value;
}
