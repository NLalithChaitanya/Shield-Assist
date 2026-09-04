/**
 * CitationMarker — the clickable citation dot in the Evidence Thread.
 * Design Direction §16: clicking opens the source document and highlights the fact.
 */

interface CitationMarkerProps {
  index: number;
  onClick?: () => void;
  active?: boolean;
}

export default function CitationMarker({ index, onClick, active }: CitationMarkerProps) {
  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-data font-semibold transition-all duration-150 cursor-pointer ml-1 align-middle ${
        active
          ? 'bg-signal text-white scale-110'
          : 'bg-signal/10 text-signal hover:bg-signal/20 hover:scale-105'
      }`}
      title={`Citation ${index + 1} — click to view source document`}
      aria-label={`View citation ${index + 1} source`}
    >
      {index + 1}
    </button>
  );
}
