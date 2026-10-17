import { requireOptionalNativeModule } from 'expo';

/** Android only: the installed build's number, and the download that hands a new APK to the system installer. */
export interface ApkUpdateNative {
  installedVersionCode(): number;
  installedVersionName(): string;
  /** False until the user allows this app to install packages (Android asks once, in system settings). */
  canInstall(): boolean;
  openInstallSettings(): void;
  /** Downloads the APK over https, then opens the system installer on it; the user confirms there. */
  downloadAndInstall(url: string): Promise<void>;
}

export default requireOptionalNativeModule<ApkUpdateNative>('ApkUpdate');
