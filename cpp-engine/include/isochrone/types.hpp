#pragma once

#include <cstddef>
#include <stdexcept>
#include <vector>

namespace isochrone {

struct Point { //二维坐标点
  double x{};
  double y{};
};

struct Sample { //采样点以及对应的耗时距离
  Point point;
  double duration_seconds{};
  double distance_meters{};
};

struct Bounds { //与坐标轴平行的矩形范围
  double min_x{};
  double max_x{};
  double min_y{};
  double max_y{};
};

struct Segment { //由两个端点确定的线段
  Point first;
  Point second;
};

using Ring = std::vector<Point>;  //按轮廓顺序排列的顶点


/// 规则网格。values 按行优先存储，大小应为 rows * columns。
/// 第 (0, 0) 个格点位于 (bounds.min_x, bounds.min_y)。

struct Grid {
  Bounds bounds;
  double step_meters{}; //步长 即为相邻节点的间隔
  std::size_t rows{};
  std::size_t columns{};
  std::vector<double> values;

  /// 按行、列访问格点值；索引越界时抛出 std::out_of_range。
  [[nodiscard]] double& at(std::size_t row, std::size_t column) {
    if (row >= rows || column >= columns) {
      throw std::out_of_range("grid index out of range");
    }
    return values[row * columns + column];
  }

/// 只读访问格点值；索引越界时抛出 std::out_of_range。
  [[nodiscard]] double at(std::size_t row, std::size_t column) const {
    if (row >= rows || column >= columns) {
      throw std::out_of_range("grid index out of range");
    }
    return values[row * columns + column];
  }


  /// 根据行、列计算格点坐标；此函数不检查索引是否越界。
  [[nodiscard]] Point point_at(std::size_t row, std::size_t column) const {
    return Point{
        bounds.min_x + static_cast<double>(column) * step_meters,
        bounds.min_y + static_cast<double>(row) * step_meters,
    };
  }
};

}  // namespace isochrone
