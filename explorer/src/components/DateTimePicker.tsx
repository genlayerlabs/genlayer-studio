'use client';

import { useState } from 'react';
import { format, parse } from 'date-fns';
import { Calendar as CalendarIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';

interface DateTimePickerProps {
  value: string; // ISO-ish string "YYYY-MM-DDTHH:mm" or ""
  onChange: (value: string) => void;
  placeholder?: string;
}

export function DateTimePicker({ value, onChange, placeholder = 'Pick date & time' }: DateTimePickerProps) {
  const [open, setOpen] = useState(false);

  // Parse current value
  const date = value ? new Date(value) : undefined;
  const hours = date ? date.getHours().toString().padStart(2, '0') : '00';
  const minutes = date ? date.getMinutes().toString().padStart(2, '0') : '00';

  const handleDateSelect = (selected: Date | undefined) => {
    if (!selected) {
      onChange('');
      return;
    }
    // Preserve existing time or default to 00:00
    const h = date ? date.getHours() : 0;
    const m = date ? date.getMinutes() : 0;
    selected.setHours(h, m, 0, 0);
    onChange(formatToParam(selected));
  };

  const handleTimeChange = (type: 'hours' | 'minutes', val: string) => {
    const num = parseInt(val, 10);
    if (isNaN(num)) return;
    const d = date ? new Date(date) : new Date();
    if (!date) {
      // If no date set yet, use today
      d.setHours(0, 0, 0, 0);
    }
    if (type === 'hours') d.setHours(Math.min(23, Math.max(0, num)));
    if (type === 'minutes') d.setMinutes(Math.min(59, Math.max(0, num)));
    onChange(formatToParam(d));
  };

  const handleClear = () => {
    onChange('');
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          className={cn(
            'h-9 w-[200px] justify-start text-left font-normal text-sm',
            !value && 'text-muted-foreground'
          )}
        >
          <CalendarIcon className="mr-2 h-4 w-4" />
          {date ? format(date, 'MMM d, yyyy HH:mm') : placeholder}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="single"
          selected={date}
          onSelect={handleDateSelect}
          initialFocus
        />
        <div className="border-t border-border px-3 py-2 flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Time:</span>
          <input
            type="number"
            min={0}
            max={23}
            value={hours}
            onChange={(e) => handleTimeChange('hours', e.target.value)}
            className="w-12 h-7 rounded-md border border-input bg-transparent px-2 text-sm text-center outline-none [box-shadow:none!important]"
          />
          <span className="text-muted-foreground">:</span>
          <input
            type="number"
            min={0}
            max={59}
            value={minutes}
            onChange={(e) => handleTimeChange('minutes', e.target.value)}
            className="w-12 h-7 rounded-md border border-input bg-transparent px-2 text-sm text-center outline-none [box-shadow:none!important]"
          />
          <div className="flex-1" />
          <Button variant="ghost" size="sm" onClick={handleClear} className="text-xs">
            Clear
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function formatToParam(d: Date): string {
  const pad = (n: number) => n.toString().padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
