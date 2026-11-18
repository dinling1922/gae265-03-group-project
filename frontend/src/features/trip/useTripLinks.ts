import { useQuery } from "@tanstack/react-query";
import { getTripDriveFolderOptions } from "../../client/@tanstack/react-query.gen";
import type { TripOut } from "../../client/types.gen";
import type { ClosedLinks } from "../incident/closedLinks";

/**
 * 結案後的「下載全部文件」：行程的 Google Drive 資料夾；只放行 Drive 的網址（後端也擋過一次）。
 *
 * 後端要到 Drive 找資料夾，要等一下。用共用快取：送件頁結案時就開始找，回到行程頁直接用找到的網址，
 * 按鈕不會先反灰再亮。結案後資料夾不會變，找到就不再重找。
 */
export function useDriveUrl(policyId: string, locked: boolean): string | null {
  const { data } = useQuery({
    ...getTripDriveFolderOptions({ path: { policy_id: policyId } }),
    enabled: locked,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 30 * 60 * 1000,
    retry: false,
  });
  const folder = locked ? data?.url : null;
  return folder?.startsWith("https://drive.google.com/") ? folder : null;
}

/** 行程結案後的連結（結案方式、Gmail、Drive）：只放行 Gmail 與 Google Drive 的網址。 */
export function useTripLinks(policyId: string, trip: TripOut | undefined): ClosedLinks {
  const driveUrl = useDriveUrl(policyId, trip?.locked ?? false);
  const gmail = trip?.submission.gmail_url;
  return {
    closedAs: trip?.submission.status ?? null,
    gmailUrl: gmail?.startsWith("https://mail.google.com/") ? gmail : null,
    driveUrl,
  };
}
