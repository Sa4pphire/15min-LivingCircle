#pragma once

#include <stdexcept>

namespace isochrone {

class OriginNotOnWalkway : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

class AmbiguousOriginSide : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

}  // namespace isochrone
