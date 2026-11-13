import { fetchBackend } from '@/lib/fetchBackend';
import { LLMProvider } from '@/lib/types';
import { ProvidersContent } from './ProvidersContent';
import { Card, CardContent } from '@/components/ui/card';

export default async function ProvidersPage() {
  let data: { providers: LLMProvider[] } | null = null;
  let error: unknown = null;

  try {
    data = await fetchBackend<{ providers: LLMProvider[] }>('/providers');
  } catch (err) {
    error = err;
  }

  if (error || !data) {
    return (
      <Card className="border-destructive">
        <CardContent className="p-6">
          <h2 className="font-bold mb-2 text-destructive">Error loading providers</h2>
          <p className="text-destructive/80">
            {error instanceof Error ? error.message : 'Unknown error'}
          </p>
        </CardContent>
      </Card>
    );
  }

  return <ProvidersContent providers={data.providers} />;
}
