"use client";

import { createContext, useContext, useMemo, useState } from "react";

type Store = {
  files: Partial<Record<string, File>>;
  put: (id: string, file: File | null) => void;
  rates: string;
  setRates: (rates: string) => void;
};

const FileContext = createContext<Store | null>(null);

export const DEFAULT_RATES = "card=2.00,upi=0,netbanking=2.00,wallet=2.00,emi=2.00";

export function FileProvider({ children }: { children: React.ReactNode }) {
  const [files, setFiles] = useState<Partial<Record<string, File>>>({});
  const [rates, setRates] = useState(DEFAULT_RATES);
  const value = useMemo<Store>(
    () => ({
      files,
      put: (id, file) =>
        setFiles((prior) => {
          const next = { ...prior };
          if (file) next[id] = file;
          else delete next[id];
          return next;
        }),
      rates,
      setRates,
    }),
    [files, rates],
  );
  return <FileContext.Provider value={value}>{children}</FileContext.Provider>;
}

export function useFiles(): Store {
  const store = useContext(FileContext);
  if (!store) throw new Error("useFiles must be used inside FileProvider");
  return store;
}
