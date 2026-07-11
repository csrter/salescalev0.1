/**
 * Lucide icon re-exports with the app's defaults baked in (DESIGN.md errata
 * note 2): size 18 (pass size={16} in dense contexts), strokeWidth 1.75,
 * color inherited via currentColor. Import icons from here — never from
 * "lucide-react" directly — so the convention holds app-wide.
 */

import { createElement } from "react";
import type { LucideIcon, LucideProps } from "lucide-react";
import {
  AlertTriangle as LAlertTriangle,
  ArrowDown as LArrowDown,
  ArrowDownRight as LArrowDownRight,
  ArrowUp as LArrowUp,
  ArrowUpRight as LArrowUpRight,
  BarChart3 as LBarChart3,
  Building2 as LBuilding2,
  Check as LCheck,
  ChevronDown as LChevronDown,
  ChevronLeft as LChevronLeft,
  ChevronRight as LChevronRight,
  ChevronUp as LChevronUp,
  Command as LCommand,
  Compass as LCompass,
  CreditCard as LCreditCard,
  Eye as LEye,
  GitBranch as LGitBranch,
  GripVertical as LGripVertical,
  Inbox as LInbox,
  Info as LInfo,
  LayoutDashboard as LLayoutDashboard,
  Link2 as LLink2,
  Loader2 as LLoader2,
  LogOut as LLogOut,
  Megaphone as LMegaphone,
  MessageSquare as LMessageSquare,
  Moon as LMoon,
  Palette as LPalette,
  Pencil as LPencil,
  Plus as LPlus,
  RefreshCw as LRefreshCw,
  Search as LSearch,
  Send as LSend,
  Settings as LSettings,
  Shield as LShield,
  Sun as LSun,
  Table2 as LTable2,
  Trash2 as LTrash2,
  Users as LUsers,
  X as LX,
} from "lucide-react";

function withDefaults(Icon: LucideIcon, name: string): LucideIcon {
  const Wrapped = (props: LucideProps) =>
    createElement(Icon, { size: 18, strokeWidth: 1.75, ...props });
  Wrapped.displayName = name;
  return Wrapped as unknown as LucideIcon;
}

export const AlertTriangle = withDefaults(LAlertTriangle, "AlertTriangle");
export const ArrowDown = withDefaults(LArrowDown, "ArrowDown");
export const ArrowDownRight = withDefaults(LArrowDownRight, "ArrowDownRight");
export const ArrowUp = withDefaults(LArrowUp, "ArrowUp");
export const ArrowUpRight = withDefaults(LArrowUpRight, "ArrowUpRight");
export const BarChart3 = withDefaults(LBarChart3, "BarChart3");
export const Building2 = withDefaults(LBuilding2, "Building2");
export const Check = withDefaults(LCheck, "Check");
export const ChevronDown = withDefaults(LChevronDown, "ChevronDown");
export const ChevronLeft = withDefaults(LChevronLeft, "ChevronLeft");
export const ChevronRight = withDefaults(LChevronRight, "ChevronRight");
export const ChevronUp = withDefaults(LChevronUp, "ChevronUp");
export const Command = withDefaults(LCommand, "Command");
export const Compass = withDefaults(LCompass, "Compass");
export const CreditCard = withDefaults(LCreditCard, "CreditCard");
export const Eye = withDefaults(LEye, "Eye");
export const GitBranch = withDefaults(LGitBranch, "GitBranch");
export const GripVertical = withDefaults(LGripVertical, "GripVertical");
export const Inbox = withDefaults(LInbox, "Inbox");
export const Info = withDefaults(LInfo, "Info");
export const LayoutDashboard = withDefaults(LLayoutDashboard, "LayoutDashboard");
export const Link2 = withDefaults(LLink2, "Link2");
export const Loader2 = withDefaults(LLoader2, "Loader2");
export const LogOut = withDefaults(LLogOut, "LogOut");
export const Megaphone = withDefaults(LMegaphone, "Megaphone");
export const MessageSquare = withDefaults(LMessageSquare, "MessageSquare");
export const Moon = withDefaults(LMoon, "Moon");
export const Palette = withDefaults(LPalette, "Palette");
export const Pencil = withDefaults(LPencil, "Pencil");
export const Plus = withDefaults(LPlus, "Plus");
export const RefreshCw = withDefaults(LRefreshCw, "RefreshCw");
export const Search = withDefaults(LSearch, "Search");
export const Send = withDefaults(LSend, "Send");
export const Settings = withDefaults(LSettings, "Settings");
export const Shield = withDefaults(LShield, "Shield");
export const Sun = withDefaults(LSun, "Sun");
export const Table2 = withDefaults(LTable2, "Table2");
export const Trash2 = withDefaults(LTrash2, "Trash2");
export const Users = withDefaults(LUsers, "Users");
export const X = withDefaults(LX, "X");

export type { LucideIcon, LucideProps };
