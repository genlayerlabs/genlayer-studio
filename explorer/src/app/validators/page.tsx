import { fetchBackend } from '@/lib/fetchBackend';
import { Validator } from '@/lib/types';
import { ValidatorsContent } from './ValidatorsContent';
import { Card, CardContent } from '@/components/ui/card';

export default async function ValidatorsPage() {
  let data: { validators: Validator[] } | null = null;
  let error: unknown = null;

  try {
    data = await fetchBackend<{ validators: Validator[] }>('/validators');
  } catch (err) {
    error = err;
  }

  if (error || !data) {
    return (
      <Card className="border-destructive">
        <CardContent className="p-6">
          <h2 className="font-bold mb-2 text-destructive">Error loading validators</h2>
          <p className="text-destructive/80">
            {error instanceof Error ? error.message : 'Unknown error'}
          </p>
        </CardContent>
      </Card>
    );
  }

  return <ValidatorsContent validators={data.validators} />;
}
