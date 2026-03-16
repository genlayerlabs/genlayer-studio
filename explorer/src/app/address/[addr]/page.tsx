import Link from 'next/link';
import { fetchBackend } from '@/lib/fetchBackend';
import { AddressContent, type AddressInfo } from './AddressContent';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { ArrowLeft } from 'lucide-react';

export default async function AddressPage({ params }: { params: Promise<{ addr: string }> }) {
  const { addr } = await params;

  try {
    const data = await fetchBackend<AddressInfo>(
      `/address/${encodeURIComponent(addr)}`,
    );
    return <AddressContent addr={addr} data={data} />;
  } catch (err) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/" className="flex items-center gap-2">
            <ArrowLeft className="w-4 h-4" />
            Back to dashboard
          </Link>
        </Button>
        <Card className="border-destructive">
          <CardContent className="p-6">
            <h2 className="font-bold mb-2 text-destructive">Error</h2>
            <p className="text-destructive/80">
              {err instanceof Error ? err.message : 'Unknown error'}
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }
}
