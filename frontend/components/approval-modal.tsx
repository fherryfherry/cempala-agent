"use client";

import { useState } from "react";
import { formatAgentName } from "@/lib/api";
import { usePmApproval } from "@/lib/use-pm-approval";
import { parseChoices } from "@/lib/parse-choices";
import { AgentAvatar } from "@/components/agent-avatar";
import { Markdown } from "@/components/markdown";
import { ChoicePills } from "@/components/choice-pills";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** Global "PM wants your approval" popup, mounted once in the workspace layout
 * (app/w/[key]/layout.tsx) so it appears on top of whatever page the user is
 * on — not just the chat surfaces where the ChoicePills already render inline.
 * Shares detection with the floating chat via usePmApproval, so both agree on
 * what counts as a pending approval.
 *
 * `suppressed` is set by the layout when the same pills are already visible
 * inline (the full /chat page, or the floating panel open) — each surface has
 * its own sendMutation instance, so showing two at once risks the same answer
 * being submitted twice. */
export function ApprovalModal({
  workspaceId,
  suppressed,
}: {
  workspaceId: string | undefined;
  suppressed: boolean;
}) {
  const { pm, lastMessage, activeChoices, sendMutation } = usePmApproval(workspaceId);
  const [dismissedId, setDismissedId] = useState<string | null>(null);

  if (suppressed || !pm || !lastMessage || !activeChoices) return null;

  const open = lastMessage.id !== dismissedId;
  const { cleanedBody } = parseChoices(lastMessage.body);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setDismissedId(lastMessage.id);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <div className="flex items-center gap-2">
            <AgentAvatar
              name={pm.name}
              template={pm.avatar_template}
              color={pm.avatar_color}
              size={28}
            />
            <DialogTitle>{formatAgentName(pm.name, pm.role)} minta persetujuan</DialogTitle>
          </div>
        </DialogHeader>
        <div className="text-sm text-foreground">
          <Markdown>{cleanedBody}</Markdown>
        </div>
        <ChoicePills
          group={activeChoices}
          disabled={sendMutation.isPending}
          onAnswer={(text) => {
            sendMutation.mutate(text);
            setDismissedId(lastMessage.id);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
