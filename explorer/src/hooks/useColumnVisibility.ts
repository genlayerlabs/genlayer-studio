'use client';

import { useState, useEffect, useCallback } from 'react';

export interface ColumnDef {
  id: string;
  label: string;
  defaultVisible: boolean;
  alwaysVisible?: boolean;
}

function getDefaultVisibility(columns: ColumnDef[]): Record<string, boolean> {
  const defaults: Record<string, boolean> = {};
  for (const col of columns) {
    defaults[col.id] = col.defaultVisible;
  }
  return defaults;
}

function loadColumnVisibility(storageKey: string, columns: ColumnDef[]): Record<string, boolean> {
  if (typeof window === 'undefined') return getDefaultVisibility(columns);
  try {
    const stored = localStorage.getItem(storageKey);
    if (stored) {
      const parsed = JSON.parse(stored);
      // Merge with defaults so new columns get their default value
      const defaults = getDefaultVisibility(columns);
      return { ...defaults, ...parsed };
    }
  } catch {
    // ignore
  }
  return getDefaultVisibility(columns);
}

export function useColumnVisibility(storageKey: string, columns: ColumnDef[]) {
  const [columnVisibility, setColumnVisibility] = useState<Record<string, boolean>>(
    () => getDefaultVisibility(columns)
  );
  const [showColumnPicker, setShowColumnPicker] = useState(false);

  // Load from localStorage on mount
  useEffect(() => {
    setColumnVisibility(loadColumnVisibility(storageKey, columns));
  }, [storageKey, columns]);

  const toggleColumn = useCallback((columnId: string) => {
    setColumnVisibility(prev => {
      const next = { ...prev, [columnId]: !prev[columnId] };
      localStorage.setItem(storageKey, JSON.stringify(next));
      return next;
    });
  }, [storageKey]);

  const isVisible = (columnId: string) => {
    return columnVisibility[columnId] !== false;
  };

  return {
    columnVisibility,
    setColumnVisibility,
    isVisible,
    showColumnPicker,
    setShowColumnPicker,
    toggleColumn,
  };
}
