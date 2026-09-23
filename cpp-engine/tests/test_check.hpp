#pragma once

#include <stdexcept>
#include <string>

// Unlike assert(), this check is active in both Debug and Release builds.
#define TEST_CHECK(condition)                                                \
  do {                                                                       \
    if (!(condition)) {                                                      \
      throw std::runtime_error(std::string("check failed: ") + #condition + \
                               " at " + __FILE__ + ":" +                    \
                               std::to_string(__LINE__));                   \
    }                                                                        \
  } while (false)
