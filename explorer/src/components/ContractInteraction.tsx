'use client';

import { useState, useEffect } from 'react';
import { CodeBlock } from '@/components/CodeBlock';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Loader2, AlertCircle, Eye, Pencil, FileCode, Info } from 'lucide-react';
import { MethodForm } from '@/components/MethodForm';
import {
  fetchContractSchema,
  type ContractSchema,
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
