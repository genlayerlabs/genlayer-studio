import { describe, it, expect } from 'vitest';
import { useFileName } from '@/hooks';

describe('useFileName composable', () => {
  const { cleanupFileName } = useFileName();

  it('should return the original name with ".py" when there is no period in the name', () => {
    const result = cleanupFileName('example');
    expect(result).toBe('example.py');
  });

  it('should replace the extension with ".py" if there is a period in the name', () => {
    const result = cleanupFileName('document.txt');
    expect(result).toBe('document.py');
  });

  it('should return ".py" if the name is just a period', () => {
    const result = cleanupFileName('.');
    expect(result).toBe('.py');
  });

  it('should handle names with multiple periods correctly', () => {
    const result = cleanupFileName('archive.tar.gz');
    expect(result).toBe('archive.py');
  });

  it('should return ".py" for an empty string', () => {
    const result = cleanupFileName('');
    expect(result).toBe('.py');
  });
});
