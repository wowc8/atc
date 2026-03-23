import "./ResizeHandle.css";

interface Props {
  direction: "row" | "col";
  onMouseDown: (e: React.MouseEvent<HTMLDivElement>) => void;
}

export default function ResizeHandle({ direction, onMouseDown }: Props) {
  return (
    <div
      className={`resize-handle resize-handle--${direction}`}
      onMouseDown={onMouseDown}
    />
  );
}
