import echarts from 'echarts/lib/echarts'
import echartsgl from 'echarts-gl'

import Vue from 'vue'
// TODO: replace this with vue-echart
export default {
  name: "chart-component",
  data() {
    return {
      graph: null,
      graphDateRange: null,
      x: null,
      y: null
    }
  },
  props: {
    dataset: {
      type: Array
    },
    type: {
      type: String
    },
    profileIds: {
      type: Array
    }
  },

  mounted() {
    this.createGraph()
    this.updateGraph()

  },
  watch: {
    profileIds: {
      handler() {
        this.updateGraph()
      }
    }
  },
  methods: {
    saveCsv() {
      console.log('yes')
    },

    saveImage() {
      let src = this.graph.getDataURL({
        pixelRatio: 2,
        backgroundColor: '#fff'
      })
      let download = document.createElement('a')
      download.href = src
      download.download = 'screenshot.png'
      download.click();
      //  let img = new Image();
      // img.src = src

    },
    createGraph() {
      console.log('create graph')
      var dom = document.getElementById("chart-container")
      this.graph = echarts.init(dom)
    },
    updateGraph() {
      const ids = this.profileIds.join('&cdi_ids=')

      fetch(`http://localhost:5000/api/get_profiles?cdi_ids=${ids}`, {
          mode: 'cors',
          headers: {
            'Content-Type': 'application/json',
          }
        })
        .then(response => {
          const result = response.json()
          console.log('result', result)
          return result
        })
        .then(json => {
          console.log('fetchted', json)
          const data = json.data
          const symbolSize = 2.5
          let options = {
            grid3D: {},
            xAxis3D: {
              name: 'Latitude'
            },
            yAxis3D: {
              name: 'Longitude'
            },
            zAxis3D: {
              name: 'Depth',
              inverse: true
            },
            visualMap: [{
              dimension: 0,
              max: 20,
              min: 10,
              inRange: {
                color: ['#1710c0', '#0b9df0', '#00fea8', '#00ff0d', '#f5f811', '#f09a09', '#fe0300']
              },
              textStyle: {
                color: '#000'
              },
            }],
            dataset: {
              source: data
            },
            series: [{
              name: 'scattersdc',
              type: 'scatter3D',
              symbolSize: symbolSize,
              encode: {
                x: 'lat',
                y: 'lon',
                z: 'Depth',
                color: 'Water temperature',
                tooltip: [0, 1, 2, 3, 4]
              }
            }]
          };
          this.graph.setOption(options)
          // this.graph.setOption(this.option, true)
        })

    }
  }
}
