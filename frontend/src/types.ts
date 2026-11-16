export type Preview = {
  documentId: string;
  title: string;
  agencyId: string;
  documentType: string;
  docketId: string;
  openForComment: boolean;
  commentStartDate: string;
  commentEndDate: string;
  totalComments: number;
  estimatedApiCalls: number;
  estimatedHours: number;
  ratePerHour: number;
  proposedRule: boolean;
  warning: string | null;
  regulationsGovUrl: string;
};

export type Job = {
  jobId: string;
  userId: string;
  documentId: string;
  status: "queued" | "running" | "succeeded" | "failed";
  createdAt: string;
  updatedAt: string;
  queued: number;
  fetched: number;
  attachmentsDownloaded: number;
  attachmentFailures: number;
  totalComments: number | null;
  title: string;
  error: string;
  commentCount?: number;
  formLetterCount?: number;
  uniqueCount?: number;
};

export type Summary = {
  commentCount: number;
  formLetterCount: number;
  uniqueCount: number;
  attachmentCount: number;
  attachmentsDownloaded: number;
  commentsOverTime: { date: string; count: number }[];
  topOrganizations: { name: string; count: number }[];
  attachmentTypes: { format: string; count: number }[];
  clusters: {
    clusterId: string;
    count: number;
    representativeId: string;
    representativeText: string;
  }[];
};

export type CommentRow = {
  id: string;
  documentId: string;
  title: string;
  postedDate: string;
  organization: string;
  comment: string;
  attachmentCount: string;
  attachmentFiles: string;
  clusterId: string;
  isFormLetter: string;
  commentUrl: string;
};

export type CommentPage = {
  page: number;
  pageSize: number;
  total: number;
  comments: CommentRow[];
};

export type Watch = {
  documentId: string;
  createdAt: string;
  lastCheckedAt: string;
  webhookConfigured: boolean;
};
