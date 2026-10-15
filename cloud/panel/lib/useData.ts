"use client";
import { useCallback, useEffect, useRef, useState } from "react";

/** Oddiy yuklash: {data, error, loading, reload}. reload — jonli xabar kelganda chaqiriladi (birlashtiriladi). */
export function useData<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  const loadRef = useRef(load);
  loadRef.current = load;

  const run = useCallback(() => {
    const my = ++seq.current;
    loadRef
      .current()
      .then((d) => {
        if (my === seq.current) {
          setData(d);
          setError(null);
        }
      })
      .catch((e: Error) => {
        if (my === seq.current) setError(e.message);
      })
      .finally(() => {
        if (my === seq.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    setLoading(true);
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const reload = useCallback(
    (delay = 250) => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(run, delay);
    },
    [run],
  );

  useEffect(() => () => window.clearTimeout(timer.current), []);
  return { data, error, loading, reload, setData };
}

/** Har N soniyada qayta yuklash (jonli oqim uzilganda ham ma'lumot eskirmasin) */
export function useInterval(fn: () => void, ms: number) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") ref.current();
    }, ms);
    return () => window.clearInterval(id);
  }, [ms]);
}
