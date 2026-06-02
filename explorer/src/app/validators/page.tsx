import { fetchBackend } from '@/lib/fetchBackend';
import { Validator } from '@/lib/types';
import { ValidatorsContent } from './ValidatorsContent';
import { Card, CardContent } from '@/components/ui/card';

export default async function ValidatorsPage() {
  try {
    const data = await fetchBackend<{ validators: Validator[] }>('/validators');
    return <ValidatorsContent validators={data.validators} />;
  } catch (err) {
    return (
      <Card className="border-destructive">
        <CardContent className="p-6">
          <h2 className="font-bold mb-2 text-destructive">Error loading validators</h2>
          <p className="text-destructive/80">
            {err instanceof Error ? err.message : 'Unknown error'}
          </p>
        </CardContent>
      </Card>
    );
  }
}
