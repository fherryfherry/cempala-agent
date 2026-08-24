"use client"

import * as React from "react"
import { Tooltip as TooltipPrimitive } from "@base-ui/react/tooltip"

import { cn } from "@/lib/utils"

function TooltipProvider({ children, ...props }: TooltipPrimitive.Provider.Props) {
  return (
    <TooltipPrimitive.Provider
      data-slot="tooltip-provider"
      delay={150}
      closeDelay={100}
      {...props}
    >
      {children}
    </TooltipPrimitive.Provider>
  )
}

function Tooltip({ ...props }: TooltipPrimitive.Root.Props) {
  return <TooltipPrimitive.Root data-slot="tooltip" {...props} />
}

function TooltipTrigger({ ...props }: TooltipPrimitive.Trigger.Props) {
  return <TooltipPrimitive.Trigger data-slot="tooltip-trigger" {...props} />
}

function TooltipContent({
  className,
  positionerClassName,
  sideOffset = 6,
  children,
  ...props
}: TooltipPrimitive.Positioner.Props & {
  sideOffset?: number
  positionerClassName?: string
}) {
  return (
    <TooltipPrimitive.Portal data-slot="tooltip-portal">
      <TooltipPrimitive.Positioner
        data-slot="tooltip-positioner"
        sideOffset={sideOffset}
        align="center"
        className={positionerClassName}
        {...props}
      >
        <TooltipPrimitive.Popup
          data-slot="tooltip-content"
          className={cn(
            "relative z-50 max-w-xs rounded-lg bg-foreground px-2.5 py-1.5 text-xs font-medium text-background shadow-lg duration-150 outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
            className,
          )}
        >
          {children}
          <TooltipPrimitive.Arrow
            data-slot="tooltip-arrow"
            className="fill-foreground size-1.5 translate-y-px"
          />
        </TooltipPrimitive.Popup>
      </TooltipPrimitive.Positioner>
    </TooltipPrimitive.Portal>
  )
}

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider }
