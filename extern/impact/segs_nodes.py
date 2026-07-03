# Modified for ComfyUI_Eclipse (GPLv3).
import sys
import logging
from typing import Callable, Union

class SEGSOrderedFilter:
    @staticmethod
    def get_sort_key_fn(target: str) -> Union[Callable, None]:
        if target == "none":
            return None

        def sort_key_fn(seg):
            x1, y1, x2, y2 = seg.crop_region
            if target == "confidence": return seg.confidence
            if target == "area(=w*h)": return (x2 - x1) * (y2 - y1)
            if target == "width": return x2 - x1
            if target == "height": return y2 - y1
            if target == "x1": return x1
            if target == "y1": return y1
            if target == "x2": return x2
            if target == "y2": return y2
            raise Exception(f"[Eclipse Impact] SEGSOrderedFilter - Unexpected target '{target}'")

        return sort_key_fn

    def doit(self, segs, target, order, take_start, take_count):
        sort_key_fn = SEGSOrderedFilter.get_sort_key_fn(target)

        sorted_list = list(segs[1])
        if sort_key_fn is not None:
            sorted_list.sort(key=sort_key_fn, reverse=order)

        take_stop = take_start + take_count
        return (segs[0], sorted_list[take_start:take_stop]), \
            (segs[0], sorted_list[:take_start] + sorted_list[take_stop:]),

class SEGSRangeFilter:
    def doit(self, segs, target, mode, min_value, max_value):
        new_segs = []
        remained_segs = []

        for seg in segs[1]:
            x1 = seg.crop_region[0]
            y1 = seg.crop_region[1]
            x2 = seg.crop_region[2]
            y2 = seg.crop_region[3]

            if target == "area(=w*h)":
                value = (y2 - y1) * (x2 - x1)
            elif target == "length_percent":
                h = y2 - y1
                w = x2 - x1
                value = max(h/w, w/h)*100
            elif target == "width":
                value = x2 - x1
            elif target == "height":
                value = y2 - y1
            elif target == "x1":
                value = x1
            elif target == "x2":
                value = x2
            elif target == "y1":
                value = y1
            elif target == "y2":
                value = y2
            elif target == "confidence(0-100)":
                value = seg.confidence*100
            else:
                raise Exception(f"[Eclipse Impact] SEGSRangeFilter - Unexpected target '{target}'")

            if mode and min_value <= value <= max_value:
                logging.info(f"[in] value={value} / {mode}, {min_value}, {max_value}")
                new_segs.append(seg)
            elif not mode and (value < min_value or value > max_value):
                logging.info(f"[out] value={value} / {mode}, {min_value}, {max_value}")
                new_segs.append(seg)
            else:
                remained_segs.append(seg)

        return (segs[0], new_segs), (segs[0], remained_segs),

class SEGSLabelFilter:
    @staticmethod
    def filter(segs, labels):
        labels = set([label.strip() for label in labels])

        if 'all' in labels:
            return (segs, (segs[0], []), )
        else:
            res_segs = []
            remained_segs = []

            for x in segs[1]:
                if x.label in labels:
                    res_segs.append(x)
                elif 'eyes' in labels and x.label in ['left_eye', 'right_eye']:
                    res_segs.append(x)
                elif 'eyebrows' in labels and x.label in ['left_eyebrow', 'right_eyebrow']:
                    res_segs.append(x)
                elif 'pupils' in labels and x.label in ['left_pupil', 'right_pupil']:
                    res_segs.append(x)
                else:
                    remained_segs.append(x)

        return ((segs[0], res_segs), (segs[0], remained_segs), )

    def doit(self, segs, preset, labels):
        labels = labels.split(',')
        return SEGSLabelFilter.filter(segs, labels)
