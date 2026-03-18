import type { UpdateInfo } from "../../hooks/useUpdater";
import "./UpdateBanner.css";

interface UpdateBannerProps {
  updateInfo: UpdateInfo;
  status: "available" | "downloading";
  progress: number;
  onInstall: () => void;
  onDismiss: () => void;
}

export default function UpdateBanner({
  updateInfo,
  status,
  progress,
  onInstall,
  onDismiss,
}: UpdateBannerProps) {
  return (
    <div className="update-banner" data-testid="update-banner">
      <div className="update-banner__content">
        <span className="update-banner__message">
          {status === "downloading"
            ? `Downloading v${updateInfo.version}... ${progress}%`
            : `A new version is available: v${updateInfo.version}`}
        </span>
        {status === "available" && (
          <div className="update-banner__actions">
            <button
              className="update-banner__btn update-banner__btn--install"
              onClick={onInstall}
              data-testid="update-install-btn"
            >
              Install & Restart
            </button>
            <button
              className="update-banner__btn update-banner__btn--dismiss"
              onClick={onDismiss}
              data-testid="update-dismiss-btn"
            >
              Later
            </button>
          </div>
        )}
        {status === "downloading" && (
          <div className="update-banner__progress-bar">
            <div
              className="update-banner__progress-fill"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
