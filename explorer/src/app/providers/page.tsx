import { fetchBackend } from '@/lib/fetchBackend';
import { LLMProvider } from '@/lib/types';
import { ProvidersContent } from './ProvidersContent';
import { Card, CardContent } from '@/components/ui/card';

export default async function ProvidersPage() {
  try {
    const data = await fetchBackend<{ providers: LLMProvider[] }>('/providers');
    return <ProvidersContent providers={data.providers} />;
  } catch (err) {
    return (
      <Card className="border-destructive">
        <CardContent className="p-6">
          <h2 className="font-bold mb-2 text-destructive">Error loading providers</h2>
          <p className="text-destructive/80">
            {err instanceof Error ? err.message : 'Unknown error'}
          </p>
        </CardContent>
      </Card>
    );
  }
}
