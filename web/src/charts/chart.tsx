import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { EChartsOption, EChartsType } from 'echarts';

export function EChart({ option, label, height, table, className = '' }: { option: EChartsOption; label: string; height: number; table: ReactNode; className?: string }) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<EChartsType | null>(null);
  const [reached, setReached] = useState(false);
  const latest = useRef(option);
  latest.current = option;
  useEffect(() => {
    const node = container.current;
    if (!node) return;
    const visibility = new IntersectionObserver(entries => {
      for (const entry of entries) {
        // Re-arm after a full exit; small movements at the entrance threshold
        // should not repeatedly interrupt the same animation.
        if (!entry.isIntersecting || entry.intersectionRatio === 0) setReached(false);
        else if (entry.intersectionRatio >= .2) setReached(true);
      }
    }, { threshold: [0, .2], rootMargin: '0px 0px -24px 0px' });
    visibility.observe(node);
    return () => visibility.disconnect();
  }, []);
  useEffect(() => {
    if (!reached) return;
    let cancelled = false;
    let observer: ResizeObserver | undefined;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => chart.current?.setOption({ ...latest.current, animation: !motion.matches }, { replaceMerge: ['series'] });
    void import('./runtime').then(({ init }) => {
      if (cancelled || !container.current) return;
      chart.current = init(container.current, undefined, { renderer: 'svg' });
      update();
      observer = new ResizeObserver(([entry]) => {
        const instance = chart.current;
        if (!instance || !entry) return;
        const width = Math.round(entry.contentRect.width), height = Math.round(entry.contentRect.height);
        // The observer's first notification must not cancel the entrance animation.
        if (width > 0 && height > 0 && (width !== Math.round(instance.getWidth()) || height !== Math.round(instance.getHeight()))) instance.resize({ width, height });
      });
      observer.observe(container.current);
      motion.addEventListener('change', update);
    });
    return () => {
      cancelled = true;
      observer?.disconnect();
      motion.removeEventListener('change', update);
      chart.current?.dispose();
      chart.current = null;
    };
  }, [reached]);
  useEffect(() => {
    chart.current?.setOption({ ...option, animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches }, { replaceMerge: ['series'] });
  }, [option]);
  return <div className={`echart ${className}`}><div ref={container} className="echart-surface" style={{ height }} role="img" aria-label={label} data-chart-engine="echarts" data-chart-reached={reached}/><div className="sr-only">{table}</div></div>;
}
