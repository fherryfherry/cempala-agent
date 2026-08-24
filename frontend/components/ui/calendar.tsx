"use client"

import * as React from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { DayPicker } from "react-day-picker"

import { cn } from "@/lib/utils"
import { buttonVariants } from "@/components/ui/button"

function Calendar({
  className,
  classNames,
  showOutsideDays = true,
  ...props
}: React.ComponentProps<typeof DayPicker>) {
  return (
    <DayPicker
      showOutsideDays={showOutsideDays}
      className={cn("p-3", className)}
      classNames={{
        months: "flex flex-col gap-2",
        month: "flex flex-col gap-3",
        month_caption: "flex items-center justify-center pt-1 relative",
        caption_label: "text-sm font-medium",
        nav: "flex items-center justify-between absolute inset-x-1 top-1",
        button_previous: cn(
          buttonVariants({ variant: "outline", size: "icon-sm" }),
          "size-7 bg-transparent p-0",
        ),
        button_next: cn(
          buttonVariants({ variant: "outline", size: "icon-sm" }),
          "size-7 bg-transparent p-0",
        ),
        month_grid: "w-full border-collapse",
        weekdays: "flex",
        weekday: "w-8 text-center text-xs font-normal text-muted-foreground",
        week: "mt-1 flex w-full",
        day: "relative w-8 h-8 p-0 text-center text-sm focus-within:relative focus-within:z-20",
        day_button: cn(
          buttonVariants({ variant: "ghost", size: "icon-sm" }),
          "size-8 rounded-md p-0 font-normal aria-selected:opacity-100",
        ),
        range_start: "rounded-l-md bg-accent",
        range_end: "rounded-r-md bg-accent",
        selected:
          "[&>button]:bg-primary [&>button]:text-primary-foreground [&>button]:hover:bg-primary [&>button]:hover:text-primary-foreground",
        today: "[&>button]:border [&>button]:border-border",
        outside: "text-muted-foreground opacity-50",
        disabled: "text-muted-foreground opacity-50",
        hidden: "invisible",
        ...classNames,
      }}
      components={{
        Chevron: ({ orientation }) =>
          orientation === "left" ? (
            <ChevronLeft className="size-4" />
          ) : (
            <ChevronRight className="size-4" />
          ),
      }}
      {...props}
    />
  )
}

export { Calendar }
