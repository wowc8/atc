import { useState, useEffect } from "react";
import { timeAgo, parseDate } from "../../utils/dates";

interface TimeAgoProps {
  datetime: string;
  refreshMs?: number;
}

export default function TimeAgo({ datetime, refreshMs = 30_000 }: TimeAgoProps) {
  const [display, setDisplay] = useState(() => timeAgo(datetime));

  useEffect(() => {
    setDisplay(timeAgo(datetime));
    const interval = setInterval(() => {
      setDisplay(timeAgo(datetime));
    }, refreshMs);
    return () => clearInterval(interval);
  }, [datetime, refreshMs]);

  const fullDate = parseDate(datetime).toLocaleString();

  return (
    <time dateTime={datetime} title={fullDate} data-testid="time-ago">
      {display}
    </time>
  );
}
