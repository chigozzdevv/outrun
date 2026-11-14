import { init, use } from 'echarts/core';
import { BarChart, LineChart } from 'echarts/charts';
import { AriaComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';

use([BarChart, LineChart, AriaComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, SVGRenderer]);
export { init };
